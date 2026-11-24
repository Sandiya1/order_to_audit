import datetime
from decimal import Decimal as Dc

from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import StockMovement
from inventory.services import on_hand
from masters.benchmark import run_scenario
from masters.seed import seed_master_data
from masters.utils import fy_label
from sales import services as svc
from sales.models import Invoice
from sales.tax import invoice_totals, line_tax


class TaxMathTests(TestCase):
    def test_intra_state_split(self):
        self.assertEqual(line_tax(Dc("7500"), Dc("5"), False), {"cgst": Dc("187.50"), "sgst": Dc("187.50"), "igst": Dc("0")})

    def test_inter_state_is_igst(self):
        self.assertEqual(line_tax(Dc("6000"), Dc("5"), True), {"cgst": Dc("0"), "sgst": Dc("0"), "igst": Dc("300.00")})

    def test_odd_paise_never_drift(self):
        r = line_tax(Dc("33.30"), Dc("5"), False)          # tax 1.665 -> 1.67; cgst+sgst must equal 1.67
        self.assertEqual(r["cgst"] + r["sgst"], Dc("1.67"))

    def test_totals(self):
        t = invoice_totals([{"taxable": Dc("100"), "cgst": Dc("2.5"), "sgst": Dc("2.5"), "igst": Dc("0")}] * 2)
        self.assertEqual(t["total"], Dc("210.0"))

    def test_fy_label(self):
        self.assertEqual(fy_label(datetime.date(2027, 3, 31)), "2026-27")
        self.assertEqual(fy_label(datetime.date(2027, 4, 1)), "2027-28")


class SalesFlowTests(TestCase):
    def setUp(self):
        self.s = seed_master_data()
        self.mur = self.s["items"]["mur"]
        self.u = self.s["users"]

    def test_invoice_numbering_by_fy(self):
        o1, _ = svc.place_order(self.u["salesA"], self.s["retailer"], [(self.mur, 5)])
        svc.approve_order(self.u["admin"], o1)
        i1 = svc.create_invoice(self.u["admin"], o1, datetime.date(2027, 3, 31))
        o2, _ = svc.place_order(self.u["salesA"], self.s["retailer"], [(self.mur, 5)])
        svc.approve_order(self.u["admin"], o2)
        i2 = svc.create_invoice(self.u["admin"], o2, datetime.date(2027, 4, 1))
        self.assertEqual(i1.number, "INV/2026-27/0001")
        self.assertEqual(i2.number, "INV/2027-28/0001")     # sequence restarts with the financial year

    def test_offline_resubmit_is_idempotent(self):
        import uuid
        cu = uuid.uuid4()
        a, c1 = svc.place_order(self.u["salesA"], self.s["retailer"], [(self.mur, 5)], cu)
        b, c2 = svc.place_order(self.u["salesA"], self.s["retailer"], [(self.mur, 5)], cu)
        self.assertTrue(c1); self.assertFalse(c2); self.assertEqual(a.pk, b.pk)

    def test_shortage_blocks_approval_and_reserved_stock_counts(self):
        o1, _ = svc.place_order(self.u["salesA"], self.s["retailer"], [(self.mur, 40)])
        svc.approve_order(self.u["admin"], o1)                      # reserves 40 of 50
        o2, _ = svc.place_order(self.u["salesB"], self.s["distributor"], [(self.mur, 20)])
        with self.assertRaises(svc.ShortageError) as cm:
            svc.approve_order(self.u["admin"], o2)
        self.assertEqual(cm.exception.shortages[0]["shortage"], Dc("10"))
        svc.reduce_to_available(self.u["admin"], o2)
        svc.approve_order(self.u["admin"], o2)                      # now 10 fits

    def test_cancel_invoice_issues_credit_note_and_returns_stock(self):
        o, _ = svc.place_order(self.u["salesA"], self.s["retailer"], [(self.mur, 10)])
        svc.approve_order(self.u["admin"], o)
        inv = svc.create_invoice(self.u["admin"], o)
        svc.dispatch_invoice(self.u["admin"], inv)
        self.assertEqual(on_hand(self.mur), Dc("40"))
        cn = svc.cancel_invoice(self.u["admin"], inv, "wrong rate")
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.CANCELLED)
        self.assertEqual(cn.total, inv.total)
        self.assertEqual(on_hand(self.mur), Dc("50"))               # stock came back
        self.assertEqual(svc.customer_outstanding(self.s["retailer"]), Dc("0"))
        self.assertTrue(Invoice.objects.filter(pk=inv.pk).exists())  # never deleted

    def test_ageing_buckets_and_fifo_payments(self):
        r = self.s["retailer"]
        for days_ago, qty in ((70, 10), (10, 10)):
            o, _ = svc.place_order(self.u["salesA"], r, [(self.mur, qty)])
            svc.approve_order(self.u["admin"], o)
            svc.create_invoice(self.u["admin"], o, datetime.date.today() - datetime.timedelta(days=days_ago))
        svc.record_payment(self.u["admin"], r, Dc("525"))            # clears the old invoice (500 + 25 GST)
        row = svc.receivables()[0]
        self.assertEqual(row["outstanding"], Dc("525"))
        self.assertEqual(row["buckets"]["0-30"], Dc("525"))
        self.assertEqual(row["buckets"]["61-90"], Dc("0"))

    def test_payment_must_be_positive(self):
        with self.assertRaises(ValidationError):
            svc.record_payment(self.u["admin"], self.s["retailer"], Dc("0"))


class BenchmarkTests(TestCase):
    """The section 5 scenario, end to end."""
    def test_benchmark_numbers(self):
        r = run_scenario()
        b, run, inv = r["balances"], r["run"], r["invoice"]
        self.assertEqual(b["flour"], Dc("120"))
        self.assertEqual(b["oil"], Dc("19.5"))
        self.assertEqual(b["pouch"], Dc("306"))
        self.assertEqual(r["fg_after_run"], Dc("238"))
        self.assertEqual(r["fg_after_dispatch"], Dc("88"))
        self.assertEqual(b["mur"], Dc("88"))
        self.assertEqual(run.yield_variance_pct, Dc("-6.00")); self.assertTrue(run.yield_flagged)
        oil = run.lines.get(item__code="OIL")
        self.assertEqual(oil.variance_pct, Dc("5.00")); self.assertTrue(oil.flagged)
        self.assertEqual((inv.taxable_value, inv.cgst, inv.sgst, inv.igst, inv.total),
                         (Dc("7500"), Dc("187.50"), Dc("187.50"), Dc("0"), Dc("7875")))
        self.assertEqual(r["outstanding_a"], Dc("3875"))
        self.assertEqual(r["shortage"], Dc("32"))
        self.assertTrue(r["tax_b"]["igst_applies"])
        self.assertEqual(run.material_cost, Dc("4055"))
        self.assertEqual(run.total_cost, Dc("5855"))
        self.assertEqual(round(run.cost_per_packet, 2), Dc("31.14"))

    def test_ledger_reconciles_for_every_item(self):
        run_scenario()
        from django.db.models import Sum
        from masters.models import Item
        for item in Item.objects.all():
            s = StockMovement.objects.filter(item=item).aggregate(q=Sum("qty"))["q"] or 0
            self.assertEqual(s, on_hand(item))
            # every non-opening movement points back to a source document
            for m in StockMovement.objects.filter(item=item).exclude(movement_type="OPENING"):
                self.assertTrue(m.source_type and m.source_ref, m)
