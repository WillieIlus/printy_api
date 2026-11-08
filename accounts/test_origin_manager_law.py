"""Originating manager and the manager-side fee waiver (law 1-9, 12).

Every test here is written against the user-facing behaviour described in
``law.txt``: a manager who creates a client stamps that client's origin once, the
origin can never move, the Printy fee is waived only for the origin manager, and
the decision is frozen on the quote/job so preview, sent quote and final totals
agree. The printer side of the money never changes.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ClientInvite, UserProfile
from accounts.services.client_invites import (
    claim_client_invite,
    create_client_invite,
    link_client_identity,
)
from accounts.services.phone import set_profile_phone, set_origin_manager
from api.dashboard_views import _resolve_or_create_partner_client
from jobs.managed_services import create_managed_job_from_accepted_quote
from pricing.services.fee_waiver import (
    fee_waived_for_job,
    fee_waived_for_pair,
    fee_waived_for_quote,
    fee_waived_for_quote_request,
)
from pricing.services.platform_fee_policy import (
    calculate_financial_split,
    create_quote_financial_split,
    get_active_platform_fee_policy,
)
from quotes.models import Quote, QuoteRequest
from shops.models import Shop

User = get_user_model()

MANAGER_PHONE = "0722000001"
OTHER_PHONE = "0722000002"
CLIENT_PHONE = "0722000011"
SPARE_PHONE = "0722000012"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class OriginManagerLawTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.manager = self._user("manager-one@example.test", User.Role.BROKER, "Mia Manager")
        self.other_manager = self._user("manager-two@example.test", User.Role.BROKER, "Oscar Other")
        self.shop = Shop.objects.create(owner=self.manager, name="Northpress", slug="northpress")
        set_profile_phone(self.manager, MANAGER_PHONE, verified=True)
        set_profile_phone(self.other_manager, OTHER_PHONE, verified=True)

    # helpers ---------------------------------------------------------------
    def _user(self, email, role, name="", **extra):
        return User.objects.create(email=email, role=role, name=name, **extra)

    def _client_with_origin(self, *, phone=CLIENT_PHONE, email="client@example.test"):
        client = self._user(email, User.Role.CLIENT, "Cathy Client")
        set_profile_phone(client, phone, verified=True)
        set_origin_manager(client, self.manager)
        return client

    def _market_client(self, *, phone=SPARE_PHONE, email="market@example.test"):
        client = self._user(email, User.Role.CLIENT, "Mark Market")
        set_profile_phone(client, phone, verified=True)
        return client

    def _request(self, *, manager=None, client=None, phone="", name="Client"):
        manager = manager or self.manager
        return QuoteRequest.objects.create(
            created_by=manager,
            assigned_manager=manager,
            created_by_manager=manager,
            on_behalf_of=client,
            customer_name=name,
            customer_phone=phone,
            status=QuoteRequest.QUOTED,
        )

    def _quote(self, request, *, total="1600.00", status=Quote.SENT):
        quote = Quote.objects.create(
            quote_request=request,
            shop=self.shop,
            created_by=request.assigned_manager or self.manager,
            total=Decimal(total),
            status=status,
        )
        return quote

    def _split(self, quote, *, production_cost="1000.00", client_price="1600.00"):
        return create_quote_financial_split(
            quote=quote,
            production_cost=Decimal(production_cost),
            broker_client_price=Decimal(client_price),
        )

    def _job(self, request, quote):
        return create_managed_job_from_accepted_quote(
            quote_request=request, quote=quote, accepted_by=None
        )

    def _profile(self, user):
        return UserProfile.objects.get(user=user)

    # 1. a manager creating a client stamps the origin ----------------------
    def test_manager_created_client_records_that_manager_as_origin(self):
        invite, linked = create_client_invite(
            created_by=self.manager, name="Walk In", phone="0722000031"
        )
        self.assertIsNone(linked)
        self.assertEqual(invite.created_by_id, self.manager.id)

        claimed_invite, client = claim_client_invite(
            token=invite.token, password="client-pass-1", name="Walk In"
        )
        client.refresh_from_db()
        self.assertEqual(claimed_invite.status, ClientInvite.Status.CLAIMED)
        self.assertEqual(self._profile(client).origin_manager_id, self.manager.id)

        # The same stamp also happens on the send-quote step (law 8-9).
        payload = _resolve_or_create_partner_client(
            partner_user=self.manager, client_name="Ava New", client_phone="0722000032"
        )
        self.assertEqual(payload.get("status"), "invite_pending")
        second_invite = ClientInvite.objects.get(id=payload["invite_id"])
        _invite, second_client = claim_client_invite(
            token=second_invite.token, password="client-pass-2", name="Ava New"
        )
        self.assertEqual(
            self._profile(second_client).origin_manager_id, self.manager.id
        )

    # 2. the origin cannot be changed: model, API and admin ----------------
    def test_model_rejects_reassigning_or_clearing_the_origin(self):
        client = self._client_with_origin()
        profile = self._profile(client)

        profile.origin_manager = self.other_manager
        with self.assertRaises(ValidationError):
            profile.save()
        profile.refresh_from_db()
        self.assertEqual(profile.origin_manager_id, self.manager.id)

        profile.origin_manager = None
        with self.assertRaises(ValidationError):
            profile.save()
        profile.refresh_from_db()
        self.assertEqual(profile.origin_manager_id, self.manager.id)

        # The service used at creation also refuses to re-originate.
        set_origin_manager(client, self.other_manager)
        profile.refresh_from_db()
        self.assertEqual(profile.origin_manager_id, self.manager.id)

    def test_profile_api_cannot_change_the_origin(self):
        client = self._client_with_origin()
        self.api.force_authenticate(user=client)
        response = self.api.patch(
            "/api/profiles/me/",
            {"bio": "updated", "origin_manager": self.other_manager.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._profile(client).origin_manager_id, self.manager.id)
        self.assertEqual(self._profile(client).bio, "updated")

    def test_admin_shows_the_origin_but_does_not_let_it_be_edited(self):
        admin_user = self._user(
            "staff@example.test",
            User.Role.SUPER_ADMIN,
            "Staff",
            is_staff=True,
            is_superuser=True,
        )
        client = self._client_with_origin()
        self.client.force_login(admin_user)
        url = f"/admin/accounts/userprofile/{self._profile(client).id}/change/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.content.decode()
        # Read-only: the field is shown as text, never as an editable control.
        self.assertIn("origin_manager", body)
        self.assertNotIn('name="origin_manager"', body)
        self.assertNotIn("id_origin_manager", body)

    # 3. first touch wins for a phone that already exists -------------------
    def test_existing_phone_is_never_claimed_or_reoriginated(self):
        existing = self._market_client()

        invite, linked = create_client_invite(
            created_by=self.manager, name="Mark Market", phone=SPARE_PHONE
        )
        self.assertIsNone(invite)
        self.assertEqual(linked.id, existing.id)

        payload = _resolve_or_create_partner_client(
            partner_user=self.manager, client_name="Mark Market", client_phone=SPARE_PHONE
        )
        self.assertEqual(payload.get("status"), "linked")
        self.assertEqual(payload.get("client_id"), existing.id)
        self.assertIsNone(self._profile(existing).origin_manager_id)
        self.assertEqual(
            ClientInvite.objects.filter(phone_normalized__endswith="000012").count(), 0
        )

        # A second manager cannot take an invite another manager created.
        first_invite, _ = create_client_invite(
            created_by=self.manager, name="New Person", phone="0722000041"
        )
        refreshed, _ = create_client_invite(
            created_by=self.other_manager, name="New Person", phone="0722000041"
        )
        self.assertEqual(refreshed.created_by_id, self.manager.id)
        self.assertEqual(refreshed.pk, first_invite.pk)

    # 4. the origin manager's job waives the Printy fee --------------------
    def test_origin_manager_job_has_no_printy_fee(self):
        client = self._client_with_origin()
        request = self._request(client=client, phone=CLIENT_PHONE, name="Cathy")
        quote = self._quote(request)
        split = self._split(quote)

        self.assertTrue(fee_waived_for_pair(client=client, manager=self.manager))
        self.assertTrue(fee_waived_for_quote_request(request))
        self.assertTrue(split.fee_waived)
        self.assertEqual(split.printy_fee, Decimal("0.00"))
        self.assertEqual(
            split.manager_payout, split.client_total - split.shop_payout
        )

        job = self._job(request, quote)
        self.assertTrue(job.fee_waived)
        self.assertEqual(job.printy_fee, Decimal("0.00"))
        self.assertEqual(job.client_total, split.client_total)
        self.assertEqual(job.broker_payout, split.manager_payout)
        self.assertTrue(fee_waived_for_job(job))

    # 5. another manager's job pays the normal fee -------------------------
    def test_job_with_a_different_manager_pays_the_normal_fee(self):
        client = self._client_with_origin()
        request = self._request(
            manager=self.other_manager, client=client, phone=CLIENT_PHONE, name="Cathy"
        )
        quote = self._quote(request)
        split = self._split(quote)

        self.assertFalse(fee_waived_for_pair(client=client, manager=self.other_manager))
        self.assertFalse(split.fee_waived)
        self.assertGreater(split.printy_fee, Decimal("0.00"))
        self.assertLess(split.manager_payout, split.client_total - split.shop_payout)

        job = self._job(request, quote)
        self.assertFalse(job.fee_waived)
        self.assertEqual(job.printy_fee, split.printy_fee)
        self.assertGreater(job.printy_fee, Decimal("0.00"))

    # 6. coming back to the origin manager is waived again -----------------
    def test_client_returning_to_the_origin_manager_is_waived_again(self):
        client = self._client_with_origin()
        away_request = self._request(
            manager=self.other_manager, client=client, phone=CLIENT_PHONE, name="Cathy"
        )
        away_job = self._job(away_request, self._quote(away_request))
        self.assertFalse(away_job.fee_waived)

        back_request = self._request(client=client, phone=CLIENT_PHONE, name="Cathy")
        back_split = self._split(self._quote(back_request))
        self.assertTrue(back_split.fee_waived)
        self.assertEqual(back_split.printy_fee, Decimal("0.00"))

        # The earlier job keeps the decision it was priced with.
        away_job.refresh_from_db()
        self.assertFalse(away_job.fee_waived)

    # 7. preview, sent quote and final totals agree ------------------------
    def test_preview_sent_quote_and_final_totals_are_identical(self):
        client = self._client_with_origin()
        request = self._request(client=client, phone=CLIENT_PHONE, name="Cathy")
        quote = self._quote(request)

        # What a preview computes for this request/manager pair.
        preview = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1600.00"),
            policy=get_active_platform_fee_policy(),
            fee_waived=fee_waived_for_quote_request(request),
        )
        split = self._split(quote)
        self.assertEqual(split.fee_waived, preview.fee_waived)

        # The sent quote row the manager/client sees.
        self.api.force_authenticate(user=self.manager)
        row_response = self.api.get(f"/api/dashboard/partner/quotes/{request.id}/")
        self.assertEqual(row_response.status_code, 200, row_response.content)
        row = row_response.json().get("quote") or {}
        self.assertEqual(row.get("fee_waived"), preview.fee_waived)
        self.assertEqual(
            Decimal(str(row.get("client_total"))), preview.client_total
        )

        # The final job totals.
        job = self._job(request, quote)
        self.assertEqual(job.client_total, preview.client_total)
        self.assertEqual(job.broker_payout, preview.manager_payout)
        self.assertEqual(job.printy_fee, preview.printy_fee)
        for field in ("client_total", "shop_payout", "manager_payout", "printy_fee"):
            self.assertEqual(getattr(split, field), getattr(preview, field), field)

    # 8. the stored decision never changes with later data changes ---------
    def test_fee_decision_is_frozen_against_later_changes(self):
        client = self._client_with_origin()
        request = self._request(client=client, phone=CLIENT_PHONE, name="Cathy")
        quote = self._quote(request)
        split = self._split(quote)
        job = self._job(request, quote)
        self.assertTrue(split.fee_waived)

        # The manager's own record changes; the client link is cleared on the
        # request; a new fee policy is published. None of it may re-price.
        self.manager.name = "Renamed Manager"
        self.manager.role = User.Role.PARTNER
        self.manager.save(update_fields=["name", "role", "updated_at"])
        request.on_behalf_of = None
        request.save(update_fields=["on_behalf_of", "updated_at"])
        policy = get_active_platform_fee_policy()
        policy.standard_markup_fee_rate = Decimal("0.4500")
        policy.printer_fee_rate = Decimal("0.3000")
        policy.save()

        self.assertTrue(fee_waived_for_quote(quote))
        job.refresh_from_db()
        self.assertTrue(job.fee_waived)
        self.assertEqual(job.printy_fee, Decimal("0.00"))

        # Re-pricing the same quote keeps the frozen flag and its money.
        repriced = self._split(quote, client_price="1700.00")
        self.assertTrue(repriced.fee_waived)
        self.assertEqual(repriced.printy_fee, Decimal("0.00"))

    # 9. the printer side is untouched by the waiver ----------------------
    def test_printer_side_fee_is_identical_with_and_without_the_waiver(self):
        policy = get_active_platform_fee_policy()
        waived = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1600.00"),
            policy=policy,
            fee_waived=True,
        )
        unwaived = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1600.00"),
            policy=policy,
            fee_waived=False,
        )
        self.assertEqual(waived.shop_payout, unwaived.shop_payout)
        self.assertEqual(waived.printer_side_fee, unwaived.printer_side_fee)
        self.assertEqual(waived.client_total, unwaived.client_total)
        self.assertGreater(unwaived.printy_fee, waived.printy_fee)

        # And on the job rows: the printer's cut is client_total - manager payout.
        client = self._client_with_origin()
        owned_request = self._request(client=client, phone=CLIENT_PHONE, name="Cathy")
        owned_quote = self._quote(owned_request)
        self._split(owned_quote)
        owned_job = self._job(owned_request, owned_quote)
        marketplace = self._market_client(phone="0722000051", email="market2@example.test")
        market_request = self._request(
            client=marketplace, phone="0722000051", name="Mark Market"
        )
        market_quote = self._quote(market_request)
        self._split(market_quote)
        market_job = self._job(market_request, market_quote)

        self.assertTrue(owned_job.fee_waived)
        self.assertFalse(market_job.fee_waived)
        self.assertEqual(owned_job.client_total, market_job.client_total)
        # The printer keeps the same payout and the same printer-side fee on both
        # jobs; only the manager-side Printy fee moved.
        owned_quote = owned_job.source_quote
        market_quote = market_job.source_quote
        self.assertEqual(
            owned_quote.financial_split.shop_payout,
            market_quote.financial_split.shop_payout,
        )
        self.assertEqual(
            owned_quote.financial_split.printer_side_fee,
            market_quote.financial_split.printer_side_fee,
        )
        self.assertEqual(owned_job.printy_fee, Decimal("0.00"))
        self.assertLess(owned_job.printy_fee, market_job.printy_fee)

    # 10. deletion: PROTECT for the origin, SET_NULL for the rest ---------
    def test_deleting_the_origin_manager_is_protected_and_jobs_survive(self):
        client = self._client_with_origin()
        request = self._request(client=client, phone=CLIENT_PHONE, name="Cathy")
        job = self._job(request, self._quote(request))
        totals = (job.client_total, job.printy_fee, job.broker_payout)

        with self.assertRaises(ProtectedError):
            self.manager.delete()

        self.assertTrue(User.objects.filter(pk=self.manager.pk).exists())
        job.refresh_from_db()
        self.assertEqual((job.client_total, job.printy_fee, job.broker_payout), totals)
        self.assertEqual(self._profile(client).origin_manager_id, self.manager.id)

    def test_deleting_a_non_origin_manager_keeps_past_jobs_intact(self):
        client = self._client_with_origin()
        request = self._request(
            manager=self.other_manager, client=client, phone=CLIENT_PHONE, name="Cathy"
        )
        job = self._job(request, self._quote(request))
        totals = (job.client_total, job.printy_fee, job.broker_payout)

        self.other_manager.delete()

        job.refresh_from_db()
        self.assertIsNone(job.broker_id)
        self.assertEqual((job.client_total, job.printy_fee, job.broker_payout), totals)
        self.assertEqual(self._profile(client).origin_manager_id, self.manager.id)

    # 13. signing up later links the same client and keeps the origin -----
    def test_signup_with_the_captured_phone_links_the_same_client_record(self):
        invite, _ = create_client_invite(
            created_by=self.manager, name="Bob Buyer", phone="0722000061"
        )
        request = self._request(client=None, phone="0722000061", name="Bob Buyer")

        # The client signs up afterwards with the same number.
        signup = self._user("bob@example.test", User.Role.CLIENT, "Bob Buyer")
        set_profile_phone(signup, "0722000061", verified=False)
        link_client_identity(signup, "0722000061")

        profile = self._profile(signup)
        self.assertEqual(profile.origin_manager_id, self.manager.id)
        invite.refresh_from_db()
        self.assertEqual(invite.status, ClientInvite.Status.CLAIMED)
        self.assertEqual(invite.client_user_id, signup.id)
        request.refresh_from_db()
        self.assertEqual(request.on_behalf_of_id, signup.id)
        self.assertEqual(
            ClientInvite.objects.filter(phone_normalized="254722000061").count(), 1
        )
        self.assertEqual(
            UserProfile.objects.filter(
                phone_normalized="254722000061", origin_manager__isnull=False
            ).count(),
            1,
        )
        self.assertEqual(
            UserProfile.objects.get(phone_normalized="254722000061").origin_manager_id,
            self.manager.id,
        )

    def test_an_already_origin_stamped_client_is_not_claimed_by_signup(self):
        client = self._client_with_origin()
        intruder = self._user("intruder@example.test", User.Role.CLIENT, "Intruder")
        set_profile_phone(intruder, CLIENT_PHONE, verified=False)
        link_client_identity(intruder, CLIENT_PHONE)

        self.assertIsNone(self._profile(intruder).origin_manager_id)
        self.assertEqual(self._profile(client).origin_manager_id, self.manager.id)
        self.assertEqual(
            UserProfile.objects.filter(
                phone_normalized="254722000011", origin_manager__isnull=False
            ).count(),
            1,
        )
