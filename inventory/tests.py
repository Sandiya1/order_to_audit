from decimal import Decimal as Dc

from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import StockMovement
from inventory.services import InsufficientStock, create_purchase, current_wac, on_hand, post_movement, stock_summary
from masters.models import Unit, to_base
from masters.seed import seed_master_data
from masters.utils import amount_in_words, inr
import datetime


class UnitConversionTests(TestCase):
    def setUp(self):
        self.s = seed_master_data()
        self.U = {u.code: u for u in Unit.objects.all()}

    def test_mass_and_volume(self):
        flour, oil = self.s["items"]["flour"], self.s["items"]["oil"]
        self.assertEqual(to_base(500, self.U["g"], flour), Dc("0.5"))
        self.assertEqual(to_base(250, self.U["ml"], oil), Dc("0.25"))
        self.assertEqual(to_base(2, self.U["kg"], flour), Dc("2"))

    def test_carton_of_packets(self):
        mur = self.s["items"]["mur"]
        self.assertEqual(to_base(3, self.U["ctn"], mur), Dc("60"))   # 20 packets per carton

    def test_dimension_mismatch_rejected(self):
        with self.assertRaises(ValidationError):
            to_base(1, self.U["L"], self.s["items"]["flour"])


class LedgerTests(TestCase):
    def setUp(self):
        self.s = seed_master_data()
        self.flour = self.s["items"]["flour"]
        self.admin = self.s["users"]["admin"]

    def test_balance_is_sum_of_ledger(self):
        self.assertEqual(on_hand(self.flour), Dc("100"))
        post_movement(self.flour, -30, StockMovement.Type.ADJUSTMENT, "adjustment", remarks="spill")
        self.assertEqual(on_hand(self.flour), Dc("70"))

    def test_cannot_go_negative(self):
        with self.assertRaises(InsufficientStock):
            post_movement(self.flour, -101, StockMovement.Type.ADJUSTMENT, "adjustment")

    def test_rows_are_immutable(self):
        mv = StockMovement.objects.filter(item=self.flour).first()
        mv.qty = 999
        with self.assertRaises(RuntimeError):
            mv.save()
        with self.assertRaises(RuntimeError):
            mv.delete()

    def test_weighted_average_cost(self):
        kg = Unit.objects.get(code="kg")
        create_purchase(self.admin, self.s["supplier"], datetime.date.today(),
                        [dict(item=self.flour, qty=Dc("60"), unit=kg, rate=Dc("45"))])
        self.assertEqual(current_wac(self.flour), Dc("43.1250"))      # (100*42 + 60*45) / 160

    def test_purchase_in_grams_converts_and_costs_per_kg(self):
        g = Unit.objects.get(code="g")
        create_purchase(self.admin, self.s["supplier"], datetime.date.today(),
                        [dict(item=self.flour, qty=Dc("5000"), unit=g, rate=Dc("0.05"))])  # 5 kg at Rs 50/kg
        self.assertEqual(on_hand(self.flour), Dc("105"))

    def test_stock_summary_reconciles(self):
        kg = Unit.objects.get(code="kg")
        create_purchase(self.admin, self.s["supplier"], datetime.date.today(),
                        [dict(item=self.flour, qty=Dc("10"), unit=kg, rate=Dc("44"))])
        post_movement(self.flour, -4, StockMovement.Type.PRODUCTION_ISSUE, "production_run")
        row = [r for r in stock_summary(None, datetime.date.today().replace(day=1), datetime.date.today()) if r["item"] == self.flour][0]
        self.assertEqual(row["opening"] + row["receipts"] + row["issues"] + row["sales"] + row["adjustments"], row["closing"])


class FormatTests(TestCase):
    def test_indian_grouping_and_words(self):
        self.assertEqual(inr(1234567.5), "12,34,567.50")
        self.assertEqual(inr(7875), "7,875.00")
        self.assertEqual(amount_in_words(7875), "Rupees Seven Thousand Eight Hundred Seventy Five Only")
        self.assertEqual(amount_in_words(100000), "Rupees One Lakh Only")
