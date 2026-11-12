"""Tests for the public manager recommendation and buyer lock surfaces."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ManagerLock, User, UserProfile
from inventory.models import Machine, Paper
from jobs.choices import ManagedJobStatus
from jobs.models import ManagedJob
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from shops.models import Shop


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _seed_market_pricing():
    """Three real shop quotes so the public calculator returns all speed tiers."""
    PlatformFeePolicy.objects.update(is_active=False)
    PlatformFeePolicy.objects.create(
        name="Recommendation policy",
        is_active=True,
        printer_fee_rate=Decimal("0.05"),
        broker_margin_fee_rate=Decimal("0.15"),
        add_platform_fee_on_top=False,
    )
    for index, per_sheet_total in enumerate(["274.29", "308.57", "365.71"], start=1):
        owner = User.objects.create_user(
            email=f"recommendation-shop-owner-{index}@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        shop = Shop.objects.create(
            owner=owner,
            name=f"Recommendation Shop {index}",
            slug=f"recommendation-shop-{index}",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop,
            name=f"Recommendation Press {index}",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Gloss",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("1.00"),
            selling_price=Decimal(per_sheet_total),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("0.00"),
            double_price=Decimal("0.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug=f"cutting-recommendation-{index}",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("0.00"),
            is_active=True,
        )


@override_settings(PRINTY_RECOMMENDED_MANAGERS_CACHE_SECONDS=0)
class ManagerRecommendationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        _seed_market_pricing()

    def _payload(self, **overrides):
        payload = {
            "product_type": "business_card",
            "quantity": 100,
            "finished_size": "90x55mm",
            "requested_paper_category": "gloss",
            "requested_gsm": 300,
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
            "turnaround_hours": 200,
        }
        payload.update(overrides)
        return payload

    def _manager(
        self,
        *,
        email,
        name,
        verified=True,
        accepts_new_jobs=True,
        max_concurrent_jobs=3,
        partner_profile_enabled=True,
        last_assigned_at=None,
        manager_photo=None,
    ):
        user = User.objects.create_user(
            email=email,
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=partner_profile_enabled,
            name=name,
        )
        profile = UserProfile.objects.create(
            user=user,
            public_display_name=name,
            manager_verified=verified,
            manager_verified_at=timezone.now() if verified else None,
            manager_public_opt_in=True,
            manager_speciality="Same-day cards",
            accepts_new_jobs=accepts_new_jobs,
            max_concurrent_jobs=max_concurrent_jobs,
            last_assigned_at=last_assigned_at,
            manager_photo=manager_photo,
        )
        return user, profile

    def _active_job(self, manager, *, status=ManagedJobStatus.IN_PRODUCTION):
        return ManagedJob.objects.create(
            broker=manager,
            status=status,
            due_date=timezone.now() + timedelta(days=2),
        )

    def _completed_jobs(self, manager, *, count, is_late):
        due = timezone.now() - timedelta(days=1)
        return [
            ManagedJob.objects.create(
                broker=manager,
                status=ManagedJobStatus.COMPLETED,
                due_date=due,
                is_late=is_late,
                completed_at=timezone.now(),
            )
            for _ in range(count)
        ]

    def _recommend(self, *, payload=None, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client.post(
            "/api/public/recommended-managers/",
            payload or self._payload(),
            format="json",
        )

    def _tier_managers(self, response, tier):
        return response.json()["tiers"][tier]["managers"]

    def test_free_manager_ranks_before_busy_manager(self):
        free_user, _ = self._manager(email="free@test.com", name="Free Manager")
        busy_user, _ = self._manager(email="busy@test.com", name="Busy Manager")
        self._active_job(busy_user)

        response = self._recommend()
        self.assertEqual(response.status_code, 200)
        standard = self._tier_managers(response, "standard")

        self.assertEqual([row["display_name"] for row in standard[:2]], ["Free Manager", "Busy Manager"])
        self.assertTrue(standard[0]["recommended"])
        self.assertFalse(standard[1]["recommended"])
        self.assertEqual(standard[0]["availability"]["status"], "available_now")
        self.assertIn(
            standard[1]["availability"]["status"],
            {"free_this_week", "in_queue"},
        )
        self.assertEqual(standard[1]["availability"]["active_jobs"], 1)

    def test_manager_who_cannot_meet_fastest_still_appears_in_standard(self):
        free_user, _ = self._manager(email="tier-free@test.com", name="Tier Free")
        busy_user, _ = self._manager(email="tier-busy@test.com", name="Tier Busy")
        self._active_job(busy_user)

        response = self._recommend()
        standard_names = {row["display_name"] for row in self._tier_managers(response, "standard")}
        faster_names = {row["display_name"] for row in self._tier_managers(response, "faster")}
        fastest_names = {row["display_name"] for row in self._tier_managers(response, "fastest")}

        self.assertIn("Tier Busy", standard_names)
        self.assertNotIn("Tier Busy", faster_names)
        self.assertNotIn("Tier Busy", fastest_names)
        self.assertIn("Tier Free", standard_names)
        self.assertIn("Tier Free", faster_names)
        self.assertIn("Tier Free", fastest_names)

    def test_missing_turnaround_uses_stage_sla_fallback_for_ready_by(self):
        self._manager(email="fallback@test.com", name="Fallback Manager")
        payload = self._payload()
        payload.pop("turnaround_hours")

        response = self._recommend(payload=payload)
        self.assertEqual(response.status_code, 200)
        tiers = response.json()["tiers"]

        # 4 + 12 + 40 + 8 + 16 = 80 working hours. Faster is half, Fastest a
        # quarter, exactly as the configured rush factors define.
        self.assertEqual(tiers["standard"]["turnaround_hours"], 80)
        self.assertEqual(tiers["faster"]["turnaround_hours"], 40)
        self.assertEqual(tiers["fastest"]["turnaround_hours"], 20)

        row = tiers["standard"]["managers"][0]
        self.assertTrue(row["ready_by"])
        self.assertTrue(row["ready_label"])
        self.assertEqual(row["display_name"], "Fallback Manager")

    def test_on_time_tiebreak_uses_score_only_when_not_null(self):
        newer, newer_profile = self._manager(
            email="ontime@test.com",
            name="On Time Manager",
            last_assigned_at=timezone.now(),
        )
        older, older_profile = self._manager(
            email="late@test.com",
            name="Late Manager",
            last_assigned_at=timezone.now() - timedelta(days=10),
        )
        self._completed_jobs(newer, count=12, is_late=False)
        self._completed_jobs(older, count=12, is_late=True)

        response = self._recommend()
        standard = self._tier_managers(response, "standard")

        self.assertEqual(standard[0]["display_name"], "On Time Manager")
        self.assertEqual(standard[0]["on_time_pct"], 100.0)
        self.assertEqual(standard[0]["completed_jobs"], 12)
        self.assertEqual(standard[1]["on_time_pct"], 0.0)

    def test_rotation_uses_least_recently_assigned_when_scores_are_null(self):
        self._manager(
            email="recent@test.com",
            name="Recently Assigned",
            last_assigned_at=timezone.now(),
        )
        self._manager(
            email="old@test.com",
            name="Least Recently Assigned",
            last_assigned_at=timezone.now() - timedelta(days=30),
        )

        response = self._recommend()
        standard = self._tier_managers(response, "standard")

        self.assertEqual(standard[0]["display_name"], "Least Recently Assigned")
        self.assertIsNone(standard[0]["on_time_pct"])

    def test_unverified_paused_and_capacity_managers_are_excluded(self):
        self._manager(email="ok@test.com", name="Eligible Manager")
        self._manager(email="unverified@test.com", name="Unverified Manager", verified=False)
        self._manager(email="paused@test.com", name="Paused Manager", accepts_new_jobs=False)
        capacity_user, _ = self._manager(
            email="capacity@test.com",
            name="Capacity Manager",
            max_concurrent_jobs=1,
        )
        self._active_job(capacity_user)
        client = User.objects.create_user(
            email="verified-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Verified Client",
        )
        UserProfile.objects.create(
            user=client,
            public_display_name="Verified Client",
            manager_verified=True,
            manager_verified_at=timezone.now(),
            manager_public_opt_in=True,
            manager_speciality="Not a manager",
        )

        response = self._recommend()
        standard_names = {row["display_name"] for row in self._tier_managers(response, "standard")}

        self.assertEqual(standard_names, {"Eligible Manager"})

    def test_manager_without_public_opt_in_is_excluded(self):
        self._manager(email="opted-in@test.com", name="Opted In Manager")
        _, private_profile = self._manager(
            email="private@test.com", name="Private Manager"
        )
        private_profile.manager_public_opt_in = False
        private_profile.save(update_fields=["manager_public_opt_in", "updated_at"])

        response = self._recommend()
        standard_names = {
            row["display_name"] for row in self._tier_managers(response, "standard")
        }

        self.assertEqual(standard_names, {"Opted In Manager"})

    def test_manager_without_product_history_is_excluded(self):
        self._manager(email="partner@test.com", name="Partner Manager")
        self._manager(
            email="generic@test.com",
            name="Generic Manager",
            partner_profile_enabled=False,
        )

        response = self._recommend()
        standard_names = {row["display_name"] for row in self._tier_managers(response, "standard")}

        self.assertEqual(standard_names, {"Partner Manager"})

    def test_fewer_than_three_qualify_without_padding_and_none_returns_empty(self):
        user, profile = self._manager(email="single@test.com", name="Single Manager")

        response = self._recommend()
        standard = self._tier_managers(response, "standard")
        self.assertEqual(len(standard), 1)
        self.assertTrue(standard[0]["recommended"])

        profile.accepts_new_jobs = False
        profile.save(update_fields=["accepts_new_jobs", "updated_at"])

        response = self._recommend(payload=self._payload(quantity=101))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._tier_managers(response, "standard"), [])

    def test_response_never_contains_shop_identity_or_cost_fields(self):
        self._manager(email="privacy@test.com", name="Privacy Manager")
        response = self._recommend()
        payload = response.json()
        blob = str(payload)

        for forbidden in (
            "Recommendation Shop",
            "recommendation-shop",
            "production_cost",
            "printy_fee",
            "broker_payout",
            "shop_payout",
            "shop_id",
            "shop_name",
            "shop_slug",
        ):
            self.assertNotIn(forbidden, blob)

        forbidden_keys = {
            "id",
            "user_id",
            "shop",
            "shop_id",
            "shop_name",
            "shop_slug",
            "production_cost",
            "printy_fee",
            "broker_payout",
            "max_concurrent_jobs",
        }
        self.assertTrue(forbidden_keys.isdisjoint(set(_walk_keys(payload))))

    def test_photo_url_is_null_when_the_file_is_missing(self):
        user, profile = self._manager(email="photo@test.com", name="Missing Photo Manager")
        profile.manager_photo = "manager_photos/does-not-exist.jpg"
        profile.save(update_fields=["manager_photo", "updated_at"])

        response = self._recommend()
        standard = self._tier_managers(response, "standard")
        self.assertEqual(standard[0]["display_name"], "Missing Photo Manager")
        self.assertIsNone(standard[0]["photo_url"])

    def test_prices_equal_the_public_calculator_for_every_tier(self):
        self._manager(email="price@test.com", name="Price Manager")

        recommendation = self._recommend().json()
        calculator = APIClient().post(
            "/api/calculator/public-preview/",
            self._payload(),
            format="json",
        )
        self.assertEqual(calculator.status_code, 200)
        calculator_tiers = {
            option["key"]: option for option in calculator.json()["speed_options"]
        }

        for tier_key in ("standard", "faster", "fastest"):
            managers = recommendation["tiers"][tier_key]["managers"]
            self.assertTrue(managers, tier_key)
            self.assertEqual(
                managers[0]["price"]["total"],
                calculator_tiers[tier_key]["total"],
                tier_key,
            )
            self.assertEqual(
                managers[0]["price"]["per_piece"],
                f"{Decimal(calculator_tiers[tier_key]['total']) / Decimal('100'):.2f}",
                tier_key,
            )


@override_settings(PRINTY_RECOMMENDED_MANAGERS_CACHE_SECONDS=0)
class ManagerLockTestCase(TestCase):
    def setUp(self):
        _seed_market_pricing()
        self.buyer = User.objects.create_user(
            email="buyer-lock@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Lock Buyer",
        )
        self.managers = []
        for index in range(4):
            user = User.objects.create_user(
                email=f"lock-manager-{index}@test.com",
                password="pass12345",
                role=User.Role.PARTNER,
                partner_profile_enabled=True,
                name=f"Lock Manager {index}",
            )
            UserProfile.objects.create(
                user=user,
                public_display_name=f"Lock Manager {index}",
                manager_verified=True,
                manager_verified_at=timezone.now(),
                manager_public_opt_in=True,
                manager_speciality="Same-day cards",
            )
            self.managers.append(user)

    def _client(self, *, authenticated=True):
        client = APIClient()
        if authenticated:
            client.force_authenticate(user=self.buyer)
        return client

    def _put(self, index):
        profile = self.managers[index].profile
        return self._client().put(
            f"/api/buyer/locked-managers/{profile.public_id}/",
            {},
            format="json",
        )

    def _delete(self, index):
        profile = self.managers[index].profile
        return self._client().delete(f"/api/buyer/locked-managers/{profile.public_id}/")

    def test_lock_cap_is_idempotent_and_unlock_is_clean(self):
        first = self._put(0)
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["locked"])
        self.assertTrue(first.json()["created"])

        again = self._put(0)
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["created"])
        self.assertEqual(ManagerLock.objects.filter(buyer=self.buyer).count(), 1)

        self.assertEqual(self._put(1).status_code, 201)
        self.assertEqual(self._put(2).status_code, 201)

        capped = self._put(3)
        self.assertEqual(capped.status_code, 409)
        self.assertEqual(capped.json()["code"], "lock_limit_reached")

        listing = self._client().get("/api/buyer/locked-managers/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["count"], 3)
        self.assertEqual(listing.json()["cap"], 3)
        self.assertEqual(len(listing.json()["results"]), 3)

        # A lock survives the manager going full: the shortlist must not
        # silently shrink just because the manager is at capacity right now.
        full_profile = self.managers[0].profile
        full_profile.max_concurrent_jobs = 1
        full_profile.save(update_fields=["max_concurrent_jobs", "updated_at"])
        ManagedJob.objects.create(
            broker=self.managers[0],
            status=ManagedJobStatus.IN_PRODUCTION,
            due_date=timezone.now() + timedelta(days=2),
        )
        listing = self._client().get("/api/buyer/locked-managers/")
        self.assertEqual(listing.json()["count"], 3)
        full_row = next(
            row
            for row in listing.json()["results"]
            if row["public_id"] == str(full_profile.public_id)
        )
        self.assertTrue(full_row["at_capacity"])

        unlocked = self._delete(0)
        self.assertEqual(unlocked.status_code, 204)
        self.assertEqual(ManagerLock.objects.filter(buyer=self.buyer).count(), 2)
        self.assertEqual(self._delete(0).status_code, 204)

    def test_lock_refuses_manager_without_public_opt_in(self):
        profile = self.managers[0].profile
        profile.manager_public_opt_in = False
        profile.save(update_fields=["manager_public_opt_in", "updated_at"])

        response = self._put(0)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "manager_unavailable")
        self.assertFalse(ManagerLock.objects.filter(buyer=self.buyer).exists())

    def test_unauthenticated_lock_access_is_rejected(self):
        response = self._client(authenticated=False).get("/api/buyer/locked-managers/")
        self.assertIn(response.status_code, (401, 403))

    def test_is_locked_is_only_present_for_authenticated_requests(self):
        self._put(0)

        authenticated = self._client().post(
            "/api/public/recommended-managers/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
                "turnaround_hours": 100,
            },
            format="json",
        )
        self.assertEqual(authenticated.status_code, 200)
        auth_rows = authenticated.json()["tiers"]["standard"]["managers"]
        self.assertTrue(auth_rows)
        self.assertTrue(any(row["is_locked"] for row in auth_rows))

        anonymous = self._client(authenticated=False).post(
            "/api/public/recommended-managers/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
                "turnaround_hours": 100,
            },
            format="json",
        )
        self.assertEqual(anonymous.status_code, 200)
        for tier in anonymous.json()["tiers"].values():
            for row in tier["managers"]:
                self.assertNotIn("is_locked", row)

    def test_locking_unverified_paused_or_full_manager_is_refused(self):
        unverified = self.managers[0]
        unverified.profile.manager_verified = False
        unverified.profile.manager_verified_at = None
        unverified.profile.save(
            update_fields=["manager_verified", "manager_verified_at", "updated_at"]
        )
        response = self._put(0)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "manager_not_verified")

        paused = self.managers[1]
        paused.profile.accepts_new_jobs = False
        paused.profile.save(update_fields=["accepts_new_jobs", "updated_at"])
        response = self._put(1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "manager_unavailable")

        full = self.managers[2]
        full.profile.max_concurrent_jobs = 1
        full.profile.save(update_fields=["max_concurrent_jobs", "updated_at"])
        ManagedJob.objects.create(
            broker=full,
            status=ManagedJobStatus.IN_PRODUCTION,
            due_date=timezone.now() + timedelta(days=2),
        )
        response = self._put(2)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "manager_at_capacity")

        client = User.objects.create_user(
            email="lock-verified-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Lock Verified Client",
        )
        UserProfile.objects.create(
            user=client,
            public_display_name="Lock Verified Client",
            manager_verified=True,
            manager_verified_at=timezone.now(),
            manager_public_opt_in=True,
        )
        response = self._client().put(
            f"/api/buyer/locked-managers/{client.profile.public_id}/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "manager_unavailable")
