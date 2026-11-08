"""Derived "my managers" and the public featured-managers list (law 13-16).

Law 13: a client's managers are derived - the origin manager plus any manager
who has a paid job for that client - never stored, and never money-bearing.
Law 14-16: the featured list needs verification, opt-in, a photo and a one-line
speciality, carries four fields only, is empty below three managers and is
shuffled on every request.
"""
from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserProfile
from accounts.services.manager_relationships import managers_for_client
from jobs.models import ManagedJob
from quotes.models import QuoteRequest

User = get_user_model()

MY_MANAGERS_URL = "/api/client-portal/my-managers/"
FEATURED_URL = "/api/public/print-managers/featured/"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MyManagersLawTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.client_user = User.objects.create(
            email="my-managers-client@example.test", role=User.Role.CLIENT, name="Cathy Client"
        )

    def _manager(self, email, name, first_name=""):
        user = User.objects.create(
            email=email, role=User.Role.BROKER, name=name, first_name=first_name
        )
        UserProfile.objects.get_or_create(user=user)
        return user

    def _job(self, broker, *, payment_status="confirmed", status="completed"):
        request = QuoteRequest.objects.create(
            created_by=broker,
            assigned_manager=broker,
            created_by_manager=broker,
            customer_name="Someone",
        )
        return ManagedJob.objects.create(
            source_quote_request=request,
            client=self.client_user,
            broker=broker,
            status=status,
            payment_status=payment_status,
        )

    def _set_origin(self, manager):
        profile, _ = UserProfile.objects.get_or_create(user=self.client_user)
        profile.origin_manager = manager
        profile.save(update_fields=["origin_manager", "updated_at"])

    def test_list_is_origin_plus_managers_with_a_paid_job(self):
        origin = self._manager("origin@example.test", "Olive Origin", "Olive")
        worker = self._manager("worker@example.test", "Wes Worker", "Wes")
        unpaid = self._manager("unpaid@example.test", "Uma Unpaid", "Uma")
        refunded = self._manager("refunded@example.test", "Rita Refunded", "Rita")
        self._set_origin(origin)

        self._job(worker, payment_status="confirmed")
        self._job(worker, payment_status="released")
        self._job(origin, payment_status="release_ready")
        self._job(unpaid, payment_status="pending")
        self._job(refunded, payment_status="refunded", status="cancelled")

        rows = managers_for_client(self.client_user)
        ids = [row["id"] for row in rows]
        self.assertIn(origin.id, ids)
        self.assertIn(worker.id, ids)
        self.assertNotIn(unpaid.id, ids)
        self.assertNotIn(refunded.id, ids)
        self.assertEqual(ids[0], origin.id)
        self.assertEqual(len(ids), len(set(ids)))

        origin_row = next(row for row in rows if row["id"] == origin.id)
        worker_row = next(row for row in rows if row["id"] == worker.id)
        self.assertEqual(origin_row["sources"], ["origin", "paid_job"])
        self.assertEqual(origin_row["paid_jobs"], 1)
        self.assertEqual(worker_row["paid_jobs"], 2)

    def test_list_carries_no_money_and_writes_nothing(self):
        origin = self._manager("origin@example.test", "Olive Origin", "Olive")
        self._set_origin(origin)
        self._job(origin)

        before = list(UserProfile.objects.values_list("pk", "origin_manager_id"))
        rows = managers_for_client(self.client_user)
        after = list(UserProfile.objects.values_list("pk", "origin_manager_id"))
        self.assertEqual(before, after)

        blob = json.dumps(rows, default=str).lower()
        for forbidden in ("amount", "fee", "margin", "cost", "price", "shop", "payout"):
            self.assertNotIn(forbidden, blob)

    def test_endpoint_is_client_only_and_not_cached(self):
        origin = self._manager("origin@example.test", "Olive Origin", "Olive")
        self._set_origin(origin)

        self.api.force_authenticate(user=self.client_user)
        response = self.api.get(MY_MANAGERS_URL)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([row["id"] for row in body["results"]], [origin.id])
        self.assertIn("no-store", response.get("Cache-Control", ""))

        self.api.force_authenticate(user=origin)
        self.assertEqual(self.api.get(MY_MANAGERS_URL).status_code, 403)

        self.api.force_authenticate(user=None)
        self.assertIn(self.api.get(MY_MANAGERS_URL).status_code, (401, 403))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FeaturedManagersLawTests(TestCase):
    def setUp(self):
        self.api = APIClient()

    def _manager(self, email, first_name, *, verified=True, opt_in=True, photo="x", speciality="Cards", role=User.Role.BROKER, active=True):
        user = User.objects.create(
            email=email, role=role, name=f"{first_name} Manager", first_name=first_name
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.manager_verified = verified
        profile.manager_public_opt_in = opt_in
        profile.avatar = f"https://cdn.example/{photo}.jpg" if photo else ""
        profile.manager_speciality = speciality
        profile.save()
        if not active:
            user.is_active = False
            user.save(update_fields=["is_active"])
        return user

    def _featured(self):
        return self.api.get(FEATURED_URL)

    def test_below_three_qualifying_managers_returns_nothing(self):
        self._manager("f1@example.test", "Fay")
        self._manager("f2@example.test", "Femi")
        response = self._featured()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["results"], [])

        self._manager("f3@example.test", "Flo")
        self.assertEqual(len(self._featured().json()["results"]), 3)

    def test_only_verified_opted_in_profiles_with_photo_and_speciality_appear(self):
        self._manager("f1@example.test", "Fay")
        self._manager("f2@example.test", "Femi")
        self._manager("f3@example.test", "Flo")
        self._manager("nv@example.test", "Nia", verified=False)
        self._manager("no@example.test", "Ned", opt_in=False)
        self._manager("np@example.test", "Nora", photo="")
        self._manager("ns@example.test", "Nate", speciality="")
        self._manager("ia@example.test", "Ivy", active=False)
        self._manager("shop@example.test", "Sam", role=User.Role.SHOP_OWNER)

        results = self._featured().json()["results"]
        names = {row["first_name"] for row in results}
        self.assertEqual(names, {"Fay", "Femi", "Flo"})
        for excluded in ("Nia", "Ned", "Nora", "Nate", "Ivy", "Sam"):
            self.assertNotIn(excluded, names)

    def test_payload_is_four_fields_without_price_rating_or_shop(self):
        for index, name in enumerate(("Fay", "Femi", "Flo"), start=1):
            self._manager(f"f{index}@example.test", name)

        response = self._featured()
        body = response.json()
        results = body["results"]
        self.assertEqual(len(results), 3)
        row_keys = {tuple(sorted(row.keys())) for row in results}
        self.assertEqual(
            row_keys, {("first_name", "photo", "speciality", "verified")}
        )
        self.assertTrue(all(row["verified"] is True for row in results))

        blob = json.dumps(body).lower()
        for forbidden in ("price", "rating", "ranking", "shop", "printer", "score", "surname"):
            self.assertNotIn(forbidden, blob)
        self.assertIn("no-store", response.get("Cache-Control", ""))

    def test_order_is_randomized_per_request(self):
        for index, name in enumerate(("Fay", "Femi", "Flo"), start=1):
            self._manager(f"f{index}@example.test", name)

        orders = {
            tuple(row["first_name"] for row in self._featured().json()["results"])
            for _ in range(20)
        }
        self.assertGreater(len(orders), 1, orders)
