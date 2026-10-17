"""Manager-assisted, phone-first client onboarding (prompt.txt regression tests).

Covers: missing email, phone verification, duplicate phone numbers, existing
accounts, unauthorized account linking and manager permissions.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ClientInvite
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest

User = get_user_model()

PHONE = "254700000101"


class PhoneFirstOnboardingTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.manager = User.objects.create_user(
            username="onboard-manager",
            email="onboard-manager@example.com",
            password="pw-manager-1",
            role=User.Role.PARTNER,
        )
        self.other_manager = User.objects.create_user(
            email="onboard-manager-2@example.com",
            password="pw",
            role=User.Role.PARTNER,
        )
        self.printer = User.objects.create_user(
            email="onboard-printer@example.com",
            password="pw",
            role=User.Role.PRODUCTION,
        )

    # helpers -------------------------------------------------------------
    def _claim(self, token, password="client-pass-1", name="Walk In Client"):
        api = APIClient()
        return api.post(
            "/api/auth/client-invites/claim/",
            {"token": token, "password": password, "name": name},
            format="json",
        )

    # 1. invite creation (missing email)
    def test_manager_creates_phone_only_invite_and_no_account_is_created(self):
        before = get_user_model().objects.count()
        response = self._create_invite()

        self.assertEqual(response.status_code, 201, response.data)
        payload = response.data
        self.assertEqual(payload["status"], "invite_pending")
        self.assertIsNone(payload["client_id"])
        self.assertTrue(payload["is_offline"])
        self.assertTrue(payload["claim_url"])
        self.assertTrue(payload["claim_token"])

        self.assertEqual(get_user_model().objects.count(), before)
        self.assertEqual(ClientInvite.objects.count(), 1)
        invite = ClientInvite.objects.get()
        self.assertEqual(invite.created_by, self.manager)
        self.assertEqual(invite.phone_normalized, "254700000001")
        self.assertEqual(invite.status, ClientInvite.Status.PENDING)

    def _create_invite(self, phone="0700000001", **extra):
        self.api.force_authenticate(self.manager)
        body = {"name": "Walk In Client", "phone": phone}
        body.update(extra)
        return self.api.post("/api/dashboard/partner/clients/", body, format="json")

    def test_reinviting_same_phone_reuses_the_pending_invite(self):
        first = self._create_invite()
        previous_token = first.data["claim_token"]
        second = self._create_invite(name="Updated Name")

        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(ClientInvite.objects.count(), 1)
        self.assertNotEqual(second.data["claim_token"], previous_token)
        self.assertEqual(ClientInvite.objects.get().name, "Updated Name")

    # 2. claim + phone-first authentication (email optional)
    def test_claim_creates_email_less_account_and_phone_login_works(self):
        invite_response = self._create_invite(phone="0700000002")
        token = invite_response.data["claim_token"]

        claim = self._claim(token)
        self.assertEqual(claim.status_code, 201, claim.data)
        self.assertTrue(claim.data["access"])
        self.assertTrue(claim.data["refresh"])
        self.assertIsNone(claim.data["user"]["email"])

        user = get_user_model().objects.get(pk=claim.data["user"]["id"])
        self.assertIsNone(user.email)
        self.assertEqual(user.role, get_user_model().Role.CLIENT)
        profile = user.profile
        self.assertEqual(profile.phone_normalized, "254700000002")
        self.assertIsNotNone(profile.phone_verified_at)
        self.assertTrue(claim.data["user"]["is_phone_verified"])

        invite = ClientInvite.objects.get()
        self.assertEqual(invite.status, ClientInvite.Status.CLAIMED)
        self.assertEqual(invite.client_user_id, user.id)

        api = APIClient()
        login = api.post(
            "/api/auth/login/",
            {"phone": "0700000002", "password": "client-pass-1"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.assertTrue(login.data["access"])

        wrong = api.post(
            "/api/auth/login/",
            {"phone": "0700000002", "password": "nope"},
            format="json",
        )
        self.assertEqual(wrong.status_code, 400, wrong.data)

    def test_invite_claim_is_single_use(self):
        token = self._create_invite(phone="0700000003").data["claim_token"]
        self.assertEqual(self._claim(token).status_code, 201)
        second = self._claim(token)
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(
            ClientInvite.objects.get().status, ClientInvite.Status.CLAIMED
        )

    def test_expired_invite_is_rejected(self):
        token = self._create_invite(phone="0700000004").data["claim_token"]
        invite = ClientInvite.objects.get()
        invite.expires_at = timezone.now() - timedelta(hours=1)
        invite.save(update_fields=["expires_at"])
        response = self._claim(token)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(ClientInvite.objects.get().status, ClientInvite.Status.EXPIRED)

    def test_unverified_self_signup_phone_cannot_log_in(self):
        api = APIClient()
        registered = api.post(
            "/api/auth/register/",
            {
                "email": "unverified-phone@test.com",
                "password": "pw-12345678",
                "name": "Self Signup",
                "phone": "0700000005",
            },
            format="json",
        )
        self.assertEqual(registered.status_code, 201, registered.data)

        login = api.post(
            "/api/auth/login/",
            {"phone": "0700000005", "password": "pw-12345678"},
            format="json",
        )
        self.assertEqual(login.status_code, 400, login.data)

    # 3. duplicate phone numbers / existing accounts
    def test_claim_is_refused_when_phone_already_belongs_to_verified_client(self):
        first = self._create_invite(phone="0700000006")
        claim = self._claim(first.data["claim_token"])
        self.assertEqual(claim.status_code, 201, claim.data)
        user = get_user_model().objects.get(pk=claim.data["user"]["id"])
        original_password = user.password

        # A second invite for the same phone cannot be created.
        second = self._create_invite(phone="0700000006")
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data["status"], "linked")
        self.assertEqual(second.data["client_id"], user.id)

        # Even a hand-crafted pending invite cannot claim over the account.
        forged = ClientInvite.objects.create(
            created_by=self.manager,
            phone="0700000006",
            phone_normalized="254700000006",
            status=ClientInvite.Status.PENDING,
            token="forged-token-for-existing-phone",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        hijack = self._claim(forged.token, password="hijack-pass-1")
        self.assertEqual(hijack.status_code, 400, hijack.data)
        user.refresh_from_db()
        self.assertEqual(user.password, original_password)

    def test_manager_cannot_invite_a_phone_owned_by_a_manager_account(self):
        manager2 = get_user_model().objects.create_user(
            email="phone-owning-manager@test.com",
            password="pw-12345678",
            role=get_user_model().Role.PARTNER,
            phone="0700000007",
        )
        response = self._create_invite(phone="0700000007")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("manager or printer", str(response.data["detail"]))
        self.assertEqual(ClientInvite.objects.count(), 0)

    def test_phone_already_registered_without_verification_is_reused(self):
        api = APIClient()
        registered = api.post(
            "/api/auth/register/",
            {
                "email": "selfsignup-phone-holder@test.com",
                "password": "pw-12345678",
                "name": "Self Signup",
                "phone": "0700000008",
            },
            format="json",
        )
        self.assertEqual(registered.status_code, 201, registered.data)
        existing = get_user_model().objects.get(email="selfsignup-phone-holder@test.com")

        response = self._create_invite(phone="0700000008")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "linked")
        self.assertEqual(response.data["client_id"], existing.id)
        self.assertEqual(ClientInvite.objects.count(), 0)
        self.assertEqual(
            get_user_model().objects.filter(profile__phone_normalized="254700000008").count(),
            1,
        )

    def test_manager_creating_client_with_existing_client_email_links_without_duplicating(self):
        existing = get_user_model().objects.create_user(
            email="existing-client@test.com",
            password="pw-12345678",
            role=get_user_model().Role.CLIENT,
        )
        response = self._create_invite(phone="", email="existing-client@test.com")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "linked")
        self.assertEqual(response.data["client_id"], existing.id)
        self.assertEqual(ClientInvite.objects.count(), 0)
        self.assertFalse(
            get_user_model()
            .objects.filter(email="existing-client@test.com")
            .exclude(pk=existing.id)
            .exists()
        )

    def test_manager_cannot_link_a_manager_account_as_a_client(self):
        other = get_user_model().objects.create_user(
            email="not-a-client@test.com",
            password="pw-12345678",
            role=get_user_model().Role.PARTNER,
        )
        self.api.force_authenticate(self.manager)
        response = self.api.post(
            "/api/dashboard/partner/clients/",
            {"email": "not-a-client@test.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["detail"], "Existing account cannot be linked as a partner client.")
        self.assertEqual(other.role, get_user_model().Role.PARTNER)

    # 4. manager permissions
    def test_client_and_printer_cannot_create_partner_clients(self):
        for user in (
            get_user_model().objects.create_user(
                email="plain-client@test.com", password="pw-12345678", role="client"
            ),
            get_user_model().objects.create_user(
                email="plain-printer@test.com", password="pw-12345678", role="production"
            ),
        ):
            api = APIClient()
            api.force_authenticate(user)
            response = api.post(
                "/api/dashboard/partner/clients/",
                {"name": "Sneaky", "phone": "0700000009"},
                format="json",
            )
            self.assertEqual(response.status_code, 403, (user.role, response.data))

    # 5. association without takeover and without consent/acceptance
    def test_attach_client_links_draft_quote_without_takeover_or_acceptance(self):
        buyer = get_user_model().objects.create_user(
            email="attach-target@test.com",
            password="pw-12345678",
            role=get_user_model().Role.CLIENT,
        )
        original_password = buyer.password
        quote_request = QuoteRequest.objects.create(
            created_by=self.manager,
            customer_name="Walk In",
            customer_phone="0700000010",
            status=QuoteStatus.DRAFT,
            request_snapshot={},
        )

        self.api.force_authenticate(self.manager)
        response = self.api.patch(
            f"/api/dashboard/partner/quotes/{quote_request.id}/attach-client/",
            {"client_email": "attach-target@test.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        quote_request.refresh_from_db()
        buyer.refresh_from_db()
        self.assertEqual(quote_request.on_behalf_of_id, buyer.id)
        self.assertEqual(quote_request.status, QuoteStatus.DRAFT)
        self.assertEqual(buyer.password, original_password)

    def test_claiming_invite_links_pending_quote_without_accepting_it(self):
        invite_response = self._create_invite(phone="0700000011")
        token = invite_response.data["claim_token"]

        matching = QuoteRequest.objects.create(
            created_by=self.manager,
            customer_name="Walk In",
            customer_phone="0700000011",
            status=QuoteStatus.QUOTED,
            request_snapshot={"pending_client": {"phone": "0700000011", "name": "Walk In"}},
        )
        unrelated = QuoteRequest.objects.create(
            created_by=self.manager,
            customer_name="Someone Else",
            customer_phone="0700000099",
            status=QuoteStatus.QUOTED,
            request_snapshot={"pending_client": {"phone": "0700000099"}},
        )

        claim = self._claim(token)
        self.assertEqual(claim.status_code, 201, claim.data)
        client_id = claim.data["user"]["id"]

        matching.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(matching.on_behalf_of_id, client_id)
        self.assertEqual(matching.status, QuoteStatus.QUOTED)
        self.assertIsNone(unrelated.on_behalf_of_id)

    def test_claim_response_surfaces_single_use_token_and_expiry(self):
        invite_response = self._create_invite(phone="0700000012")
        self.assertEqual(invite_response.data["status"], "invite_pending")
        self.assertIsNotNone(invite_response.data["expires_at"])
        self.assertTrue(invite_response.data["claim_url"].endswith(
            f"token={invite_response.data['claim_token']}"
        ))

        claim = self._claim(invite_response.data["claim_token"])
        self.assertEqual(claim.status_code, 201, claim.data)
        replay = self._claim(invite_response.data["claim_token"], password="other-pass-1")
        self.assertEqual(replay.status_code, 400, replay.data)
        self.assertEqual(len(get_user_model().objects.filter(profile__phone_normalized="254700000012")), 1)

    # 6. email can be added later, and self-signup still requires one
    def test_phone_first_client_can_add_an_email_later(self):
        invite_response = self._create_invite(phone="0700000013")
        claim = self._claim(invite_response.data["claim_token"])
        self.assertEqual(claim.status_code, 201, claim.data)

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {claim.data['access']}")
        patched = api.patch(
            "/api/auth/me/", {"email": "added-later@test.com"}, format="json"
        )
        self.assertEqual(patched.status_code, 200, patched.data)

        user = get_user_model().objects.get(pk=claim.data["user"]["id"])
        self.assertEqual(user.email, "added-later@test.com")

        rejected = api.patch(
            "/api/auth/me/", {"email": "another@test.com"}, format="json"
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)

    def test_self_signup_still_requires_an_email(self):
        api = APIClient()
        response = api.post(
            "/api/auth/register/",
            {
                "password": "pw-12345678",
                "name": "Phone Only Signup",
                "phone": "0700000014",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
