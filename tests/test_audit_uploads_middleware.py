"""Audit #9: validated uploads, safe artwork previews and the language middleware.

Each test here failed on the code before audit #9:

* renamed files (HTML/SVG saved as .png/.pdf) were stored as artwork, proofs
  and avatars, and proofs had no type or size check at all;
* the avatar endpoint renamed any unknown extension (``.svg``) to ``.jpg``;
* the signed-out artwork preview served every type inline, PDF included,
  with no throttle on the upload;
* ``UserLanguageMiddleware`` leaked the activated language to the next
  request on the thread and ignored JWT users.
"""
from __future__ import annotations

import io
import shutil
import tempfile
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import translation
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from accounts.validators import validate_manager_photo
from common.middleware import UserLanguageMiddleware
from common.uploads import ARTWORK, AVATAR, PROOF, UploadRejected, validate_upload
from jobs.file_services import upload_artwork_for_managed_job, upload_proof_for_managed_job
from jobs.models import JobAssignment, JobFile, ManagedJob
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import PendingArtworkUpload, Quote, QuoteRequest
from quotes.pending_artwork import create_pending_artwork_upload, validate_pending_artwork_file
from shops.models import Shop

PDF = b"%PDF-1.4\n%audit artwork\n"
EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n"
DOS_EPS = b"\xc5\xd0\xd3\xc6" + b"\x00" * 28
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 16
HTML = b"<html><script>alert(document.cookie)</script></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'


def _image_bytes(fmt: str, size=(10, 10)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name: str, content: bytes, content_type: str = "application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class _MediaTempMixin:
    def setUp(self):
        super().setUp()
        self._media = tempfile.mkdtemp(prefix="printy-audit9-")
        override = override_settings(MEDIA_ROOT=self._media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self._media, ignore_errors=True)


# ---------------------------------------------------------------------------
# The shared validator
# ---------------------------------------------------------------------------


class SharedUploadValidatorTests(SimpleTestCase):
    def test_every_print_type_accepted_today_still_passes(self):
        cases = {
            "art.pdf": PDF,
            "art.ai": PDF,  # PDF-compatible Illustrator
            "legacy.ai": EPS,  # PostScript Illustrator
            "art.eps": EPS,
            "binary.eps": DOS_EPS,
            "art.png": PNG_HEAD,
            "art.jpg": JPEG_HEAD,
            "art.JPEG": JPEG_HEAD,
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                checked = validate_upload(upload(name, content), ARTWORK)
                self.assertEqual(checked.filename, name)

    def test_renamed_files_are_rejected(self):
        cases = {
            "evil.png": HTML,
            "evil.jpg": SVG,
            "evil.pdf": HTML,
            "evil.eps": PNG_HEAD,
            "evil.ai": JPEG_HEAD,
            "photo.png": JPEG_HEAD,  # a real image, but not the type it claims
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesMessage(UploadRejected, "do not match"):
                    validate_upload(upload(name, content), ARTWORK)

    def test_svg_psd_tiff_and_html_are_not_accepted(self):
        for name in ("logo.svg", "layers.psd", "scan.tiff", "page.html", "noext"):
            with self.subTest(name=name):
                with self.assertRaisesMessage(UploadRejected, "Unsupported"):
                    validate_upload(upload(name, PDF), ARTWORK)
                with self.assertRaises(UploadRejected):
                    validate_upload(upload(name, PNG_HEAD), AVATAR)

    def test_size_caps(self):
        big = upload("big.pdf", PDF)
        big.size = ARTWORK.max_bytes + 1
        with self.assertRaisesMessage(UploadRejected, "50MB"):
            validate_upload(big, ARTWORK)
        with self.assertRaisesMessage(UploadRejected, "empty"):
            validate_upload(upload("empty.pdf", b""), ARTWORK)
        avatar = upload("me.png", PNG_HEAD)
        avatar.size = AVATAR.max_bytes + 1
        with self.assertRaisesMessage(UploadRejected, "5MB"):
            validate_upload(avatar, AVATAR)

    def test_validation_leaves_the_file_readable_from_the_start(self):
        handle = upload("art.pdf", PDF)
        validate_upload(handle, PROOF)
        self.assertEqual(handle.read(), PDF)

    def test_client_content_type_is_not_trusted(self):
        checked = validate_upload(upload("art.pdf", PDF, content_type="text/html"), ARTWORK)
        self.assertEqual(checked.content_type, "application/pdf")

    def test_pending_artwork_validator_rejects_renamed_file(self):
        with self.assertRaises(ValueError):
            validate_pending_artwork_file(upload("evil.png", HTML))
        self.assertEqual(validate_pending_artwork_file(upload("ok.png", PNG_HEAD)), ("ok.png", ".png"))


# ---------------------------------------------------------------------------
# Managed-job artwork and proof uploads
# ---------------------------------------------------------------------------


class ManagedJobUploadTests(_MediaTempMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_user = User.objects.create_user(email="a9-client@test.com", password="pass12345", role="client")
        self.manager = User.objects.create_user(email="a9-manager@test.com", password="pass12345", role="broker")
        self.owner = User.objects.create_user(email="a9-owner@test.com", password="pass12345", role="shop_owner")
        self.shop = Shop.objects.create(owner=self.owner, name="A9 Shop", slug="a9-shop", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="A9 Customer",
            customer_email="a9-client@test.com",
            status=QuoteStatus.CLOSED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.owner,
            status=QuoteOfferStatus.ACCEPTED,
            total=Decimal("2500.00"),
        )
        self.managed_job = ManagedJob.objects.create(
            title="A9 job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status="assigned",
            payment_status="confirmed",
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.managed_job,
            assigned_shop=self.shop,
            source_quote=self.quote,
            status="accepted",
        )

    def _proof(self, name, content):
        return upload_proof_for_managed_job(
            managed_job=self.managed_job,
            assignment=self.assignment,
            uploaded_by=self.owner,
            file=upload(name, content),
            original_filename=name,
        )

    def test_proof_service_rejects_renamed_and_disallowed_files(self):
        for name, content in (("proof.pdf", HTML), ("proof.svg", SVG), ("proof.exe", b"MZ\x90\x00")):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self._proof(name, content)
        self.assertFalse(JobFile.objects.filter(managed_job=self.managed_job).exists())

    def test_proof_service_rejects_oversize_file(self):
        big = upload("proof.pdf", PDF)
        big.size = PROOF.max_bytes + 1
        with self.assertRaises(ValueError):
            upload_proof_for_managed_job(
                managed_job=self.managed_job, assignment=self.assignment, uploaded_by=self.owner, file=big
            )

    def test_real_proof_is_still_stored(self):
        proof = self._proof("proof.pdf", PDF)
        self.assertEqual(proof.original_filename, "proof.pdf")

    def test_proof_endpoint_returns_400_for_renamed_file(self):
        api = APIClient()
        api.force_authenticate(self.owner)
        response = api.post(
            f"/api/managed-jobs/{self.managed_job.id}/files/proofs/",
            {"file": upload("proof.png", HTML, "image/png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("do not match", response.json()["detail"])
        self.assertFalse(JobFile.objects.filter(managed_job=self.managed_job).exists())

    def test_artwork_service_rejects_renamed_file(self):
        with self.assertRaisesMessage(ValueError, "do not match"):
            upload_artwork_for_managed_job(
                managed_job=self.managed_job,
                assignment=self.assignment,
                uploaded_by=self.client_user,
                file=upload("art.pdf", HTML),
                original_filename="art.pdf",
            )

    def test_artwork_endpoint_returns_400_for_renamed_file(self):
        api = APIClient()
        api.force_authenticate(self.client_user)
        response = api.post(
            f"/api/managed-jobs/{self.managed_job.id}/files/artwork/",
            {"file": upload("art.jpg", SVG, "image/jpeg")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(JobFile.objects.filter(managed_job=self.managed_job).exists())


# ---------------------------------------------------------------------------
# Avatar and manager photo
# ---------------------------------------------------------------------------


class AvatarUploadTests(_MediaTempMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="a9-avatar@test.com", password="pass12345", role="client")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _post(self, name, content, content_type):
        return self.api.post(
            "/api/profiles/me/avatar/", {"avatar": upload(name, content, content_type)}, format="multipart"
        )

    def test_svg_avatar_is_rejected_not_renamed_to_jpg(self):
        response = self._post("me.svg", SVG, "image/svg+xml")
        self.assertEqual(response.status_code, 400, response.content)

    def test_html_renamed_to_png_is_rejected(self):
        response = self._post("me.png", HTML, "image/png")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("do not match", response.json()["avatar"][0])

    def test_oversize_avatar_is_rejected(self):
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * (AVATAR.max_bytes + 1)
        response = self._post("me.png", content, "image/png")
        self.assertEqual(response.status_code, 400, response.content)

    def test_real_avatar_is_stored_with_its_checked_extension(self):
        response = self._post("me.png", _image_bytes("PNG"), "image/png")
        self.assertEqual(response.status_code, 200, response.content)
        from accounts.models import UserProfile

        self.assertTrue(UserProfile.objects.get(user=self.user).avatar.endswith(".png"))

    @override_settings(PRINTY_MANAGER_PHOTO_MIN_DIMENSION=1)
    def test_manager_photo_whose_format_does_not_match_extension_is_rejected(self):
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "do not match"):
            validate_manager_photo(upload("photo.png", _image_bytes("JPEG"), "image/png"))
        validate_manager_photo(upload("photo.jpg", _image_bytes("JPEG"), "image/jpeg"))


# ---------------------------------------------------------------------------
# Signed-out (guest) artwork upload and preview
# ---------------------------------------------------------------------------


def _low_guest_rate(rate):
    rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    rates["guest_artwork_upload"] = rate
    return {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}


class GuestArtworkTests(_MediaTempMixin, TestCase):
    URL = "/api/calculator/artwork-upload/"

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.api = APIClient()

    def _post(self, name, content, content_type="application/octet-stream", **extra):
        return self.api.post(
            self.URL, {"session_key": "guest-a9", "file": upload(name, content, content_type)}, format="multipart", **extra
        )

    def test_renamed_html_is_rejected(self):
        response = self._post("art.png", HTML, "image/png")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(PendingArtworkUpload.objects.exists())

    def test_svg_is_rejected(self):
        response = self._post("logo.svg", SVG, "image/svg+xml")
        self.assertEqual(response.status_code, 400, response.content)

    def test_stored_content_type_comes_from_the_checked_extension(self):
        response = self._post("art.pdf", PDF, "text/html")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(PendingArtworkUpload.objects.get().content_type, "application/pdf")

    def test_oversize_body_is_refused_from_content_length(self):
        response = self.api.generic(
            "POST",
            self.URL,
            data=b"x",
            content_type="multipart/form-data; boundary=BoUnDaRy",
            CONTENT_LENGTH=str(ARTWORK.max_bytes + 10 * 1024 * 1024),
        )
        self.assertEqual(response.status_code, 413, response.content)

    def test_upload_is_throttled(self):
        with override_settings(REST_FRAMEWORK=_low_guest_rate("2/min")):
            codes = [self._post(f"art{i}.pdf", PDF).status_code for i in range(3)]
        self.assertEqual(codes, [201, 201, 429])

    def test_production_rate_is_configured(self):
        from config import settings as prod_settings

        self.assertIn("guest_artwork_upload", prod_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])

    def _preview(self, pending):
        return self.api.get(f"{self.URL}{pending.token}/preview/")

    def test_pdf_preview_is_an_attachment_with_nosniff(self):
        pending = create_pending_artwork_upload(uploaded_file=upload("art.pdf", PDF), session_key="guest-a9")
        response = self._preview(pending)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"), response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("sandbox", response["Content-Security-Policy"])

    def test_eps_preview_is_an_attachment(self):
        pending = create_pending_artwork_upload(uploaded_file=upload("art.eps", EPS), session_key="guest-a9")
        response = self._preview(pending)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_validated_png_preview_is_inline_with_nosniff(self):
        pending = create_pending_artwork_upload(
            uploaded_file=upload("art.png", _image_bytes("PNG")), session_key="guest-a9"
        )
        response = self._preview(pending)
        self.assertTrue(response["Content-Disposition"].startswith("inline"), response["Content-Disposition"])
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_legacy_unvalidated_png_row_is_served_as_attachment(self):
        # A row stored before audit #9 could hold HTML under a .png name.
        pending = create_pending_artwork_upload(
            uploaded_file=upload("art.png", _image_bytes("PNG")), session_key="guest-a9"
        )
        with open(pending.file.path, "wb") as handle:
            handle.write(HTML)
        response = self._preview(pending)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))


# ---------------------------------------------------------------------------
# UserLanguageMiddleware
# ---------------------------------------------------------------------------


class UserLanguageMiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a9-sw@test.com", password="pass12345", role="client")
        self.user.preferred_language = "sw"
        self.user.save(update_fields=["preferred_language"])
        self.factory = RequestFactory()
        translation.activate("en")
        self.addCleanup(translation.deactivate)
        self.seen = []

    def _view(self, request):
        self.seen.append(translation.get_language())
        return HttpResponse("ok")

    def test_session_user_language_does_not_leak_to_the_next_request(self):
        request = self.factory.get("/api/")
        request.user = self.user
        response = UserLanguageMiddleware(self._view)(request)
        self.assertEqual(self.seen, ["sw"])
        self.assertEqual(response["Content-Language"], "sw")
        self.assertEqual(request.LANGUAGE_CODE, "sw")
        # The thread-local is back to what it was before the request.
        self.assertEqual(translation.get_language(), "en")

    def test_language_is_restored_when_the_view_raises(self):
        def boom(request):
            raise RuntimeError("view failed")

        request = self.factory.get("/api/")
        request.user = self.user
        with self.assertRaises(RuntimeError):
            UserLanguageMiddleware(boom)(request)
        self.assertEqual(translation.get_language(), "en")

    def test_jwt_user_language_is_applied(self):
        from django.contrib.auth.models import AnonymousUser

        token = AccessToken.for_user(self.user)
        request = self.factory.get("/api/", HTTP_AUTHORIZATION=f"Bearer {token}")
        request.user = AnonymousUser()  # what AuthenticationMiddleware sees for a JWT call
        UserLanguageMiddleware(self._view)(request)
        self.assertEqual(self.seen, ["sw"])
        self.assertEqual(translation.get_language(), "en")

    def test_bad_token_is_ignored_and_the_view_still_runs(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/api/", HTTP_AUTHORIZATION="Bearer not-a-token")
        request.user = AnonymousUser()
        UserLanguageMiddleware(self._view)(request)
        self.assertEqual(self.seen, ["en"])

    def test_middleware_runs_after_authentication(self):
        order = list(settings.MIDDLEWARE)
        self.assertGreater(
            order.index("common.middleware.UserLanguageMiddleware"),
            order.index("django.contrib.auth.middleware.AuthenticationMiddleware"),
        )
