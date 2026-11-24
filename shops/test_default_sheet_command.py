from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User
from inventory.models import Machine, Paper
from shops.models import Shop


class NormalizeDefaultSheetCommandTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(
            email="default-sheet-command@test.com", password="pass12345", role="shop_owner"
        )
        self.shop = Shop.objects.create(
            owner=owner, name="All Things Printy", slug="all-things-printy"
        )
        self.machine = Machine.objects.filter(shop=self.shop, is_active=True).first()
        Machine.objects.filter(pk=self.machine.pk).update(max_width_mm=330, max_height_mm=488)
        self.paper = Paper.objects.create(
            shop=self.shop,
            name="300gsm Art Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="MATTE",
            category="matt",
            width_mm=0,
            height_mm=0,
            buying_price=Decimal("10.00"),
            selling_price=Decimal("25.00"),
            is_active=True,
        )

    def test_dry_run_then_apply_then_reverse_is_reversible(self):
        with TemporaryDirectory() as tmp:
            backup = str(Path(tmp) / "default-sheet-backup.json")
            out = StringIO()
            call_command("normalize_default_sheet", backup=backup, stdout=out)
            self.machine.refresh_from_db()
            self.paper.refresh_from_db()
            self.assertEqual((self.machine.max_width_mm, self.machine.max_height_mm), (330, 488))
            self.assertEqual((self.paper.width_mm, self.paper.height_mm), (0, 0))
            self.assertIn("needs printing price", out.getvalue())
            self.assertIn("Dry run only", out.getvalue())

            call_command("normalize_default_sheet", backup=backup, apply=True, stdout=StringIO())
            self.machine.refresh_from_db()
            self.paper.refresh_from_db()
            self.assertEqual((self.machine.max_width_mm, self.machine.max_height_mm), (320, 450))
            self.assertEqual((self.paper.width_mm, self.paper.height_mm), (320, 450))

            call_command("normalize_default_sheet", backup=backup, reverse=True, stdout=StringIO())
            self.machine.refresh_from_db()
            self.paper.refresh_from_db()
            self.assertEqual((self.machine.max_width_mm, self.machine.max_height_mm), (330, 488))
            self.assertEqual((self.paper.width_mm, self.paper.height_mm), (0, 0))

    def test_paper_created_without_a_sheet_defaults_to_sra3(self):
        from api.serializers import PaperSerializer

        serializer = PaperSerializer(
            data={
                "name": "Default stock",
                "gsm": 300,
                "paper_type": "MATTE",
                "buying_price": "10.00",
                "selling_price": "25.00",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["sheet_size"], "SRA3")
