import datetime
from decimal import Decimal as Dc

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import AuditLog
from inventory.services import InsufficientStock, on_hand
from masters.seed import seed_master_data
from production.models import ProductionRun
from production.services import cancel_run, post_run, update_overheads


class ProductionTests(TestCase):
    def setUp(self):
        self.s = seed_master_data()
        self.it, self.u = self.s["items"], self.s["users"]

    def _run(self, **kw):
        args = dict(good_output=200, wastage_packets=0)
        args.update(kw)
        return post_run(self.u["prod"], self.it["mur"], datetime.date.today(), 1, args["good_output"], args["wastage_packets"],
                        actuals=args.get("actuals"))

    def test_perfect_run_has_no_flags(self):
        run = self._run()
        self.assertFalse(run.has_alert)
        self.assertEqual(on_hand(self.it["flour"]), Dc("60"))
        self.assertEqual(on_hand(self.it["mur"]), Dc("250"))

    def test_pouch_defaults_to_good_plus_damaged(self):
        run = self._run(good_output=188, wastage_packets=6)
        self.assertEqual(run.lines.get(item__code="POUCH200").actual_qty, Dc("194"))

    def test_insufficient_raw_material_blocks_run_and_posts_nothing(self):
        with self.assertRaises(InsufficientStock):
            self._run(actuals={self.it["flour"].pk: 500})
        self.assertEqual(ProductionRun.objects.count(), 0)
        self.assertEqual(on_hand(self.it["flour"]), Dc("100"))

    def test_two_batches_scale_the_bom(self):
        run = post_run(self.u["prod"], self.it["mur"], datetime.date.today(), 2, 400, 0)
        self.assertEqual(run.expected_output, Dc("400"))
        self.assertEqual(on_hand(self.it["flour"]), Dc("20"))

    def test_overheads_change_cost_and_are_audited(self):
        run = self._run(good_output=188, wastage_packets=6)
        before = run.cost_per_packet
        update_overheads(self.u["prod"], run, 1200, 600, 0)
        run.refresh_from_db()
        self.assertGreater(run.cost_per_packet, before)
        self.assertTrue(AuditLog.objects.filter(entity="productionrun", action="edit_overheads").exists())
        # finished-goods ledger value includes the capitalised overhead
        from inventory.services import stock_value
        self.assertEqual(stock_value(self.it["mur"]), Dc("992.5") + run.total_cost)

    def test_cancel_run_reverses_everything(self):
        run = self._run(good_output=188, wastage_packets=6)
        cancel_run(self.u["prod"], run, "wrong entry")
        self.assertEqual(on_hand(self.it["flour"]), Dc("100"))
        self.assertEqual(on_hand(self.it["mur"]), Dc("50"))
        with self.assertRaises(ValidationError):
            cancel_run(self.u["prod"], run, "again")
