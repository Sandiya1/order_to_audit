import datetime
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.conf import settings
from django.http import Http404
from django.shortcuts import render

from accounts.models import User
from accounts.permissions import role_required
from inventory.models import Purchase, PurchaseLine, StockMovement
from inventory.services import ledger_rows, stock_summary
from masters.models import Item
from masters.states import STATE_NAMES
from masters.utils import money
from production.models import ProductionRun
from sales.models import CreditNote, Invoice, InvoiceLine, Payment
from sales.services import receivables

from . import exports

ZERO = Decimal("0")
READERS = (User.Role.ADMIN, User.Role.ACCOUNTANT, User.Role.OWNER)


def _tot(rows, idxs, label_col=0, label="Total", width=None):
    width = width or len(rows[0]) if rows else 0
    t = [""] * (width or 0)
    if not t:
        return None
    t[label_col] = label
    for i in idxs:
        t[i] = sum((r[i] for r in rows), ZERO)
    return t


# ------------------------------------------------------------------- report builders
def sales_register(d1, d2):
    rows = []
    for i in Invoice.objects.filter(date__range=(d1, d2)).order_by("date", "id"):
        rows.append([i.date, i.number, i.buyer_name, i.buyer_gstin or "Unregistered",
                     STATE_NAMES.get(i.place_of_supply, i.place_of_supply), i.taxable_value, i.cgst, i.sgst,
                     i.igst, i.total, i.get_status_display()])
    for c in CreditNote.objects.filter(date__range=(d1, d2)).select_related("invoice").order_by("date", "id"):
        i = c.invoice
        rows.append([c.date, c.number, i.buyer_name, i.buyer_gstin or "Unregistered",
                     STATE_NAMES.get(i.place_of_supply, i.place_of_supply), -c.taxable_value, -c.cgst, -c.sgst,
                     -c.igst, -c.total, f"Credit note vs {i.number}"])
    rows.sort(key=lambda r: (r[0], r[1]))
    headers = ["Date", "Document", "Customer", "GSTIN", "Place of supply", "Taxable", "CGST", "SGST", "IGST", "Total", "Status"]
    return {"name": "Sales register", "headers": headers, "rows": rows, "totals": _tot(rows, range(5, 10), 1, "Net total", 11)}


def customer_sales(d1, d2):
    agg = OrderedDict()
    for i in Invoice.objects.filter(date__range=(d1, d2), status="ISSUED").select_related("customer").order_by("customer__name"):
        a = agg.setdefault(i.customer_id, [i.buyer_name, 0, ZERO, ZERO, ZERO])
        a[1] += 1; a[2] += i.taxable_value; a[3] += i.total - i.taxable_value; a[4] += i.total
    rows = list(agg.values())
    return {"name": "Customer-wise sales", "headers": ["Customer", "Invoices", "Taxable", "Tax", "Total"],
            "rows": rows, "totals": _tot(rows, range(1, 5), 0, "Total", 5)}


def product_sales(d1, d2):
    agg = OrderedDict()
    for l in InvoiceLine.objects.filter(invoice__date__range=(d1, d2), invoice__status="ISSUED").select_related("item"):
        a = agg.setdefault(l.item_id, [l.description, l.hsn_code, ZERO, ZERO, ZERO, ZERO])
        a[2] += l.qty; a[3] += l.taxable; a[4] += l.cgst + l.sgst + l.igst; a[5] += l.total
    rows = list(agg.values())
    return {"name": "Product-wise sales", "headers": ["Product", "HSN", "Qty (packets)", "Taxable", "Tax", "Total"],
            "rows": rows, "totals": _tot(rows, range(2, 6), 0, "Total", 6)}


def gst_summary(d1, d2):
    """Shaped for GSTR-1 (4A/B2B, 7/B2C, 9B/CDN, 12/HSN) and GSTR-3B 3.1(a). Invoice values are as issued;
    credit notes reduce the net outward supply in the period they are issued."""
    inv_lines = list(InvoiceLine.objects.filter(invoice__date__range=(d1, d2)).select_related("invoice").order_by("invoice__date", "invoice__id"))
    b2b = [[l.invoice.buyer_gstin, l.invoice.buyer_name, l.invoice.number, l.invoice.date,
            STATE_NAMES.get(l.invoice.place_of_supply, ""), l.gst_rate, l.taxable, l.igst, l.cgst, l.sgst, l.total]
           for l in inv_lines if l.invoice.buyer_gstin]
    b2c_agg = OrderedDict()
    for l in inv_lines:
        if l.invoice.buyer_gstin:
            continue
        k = (l.invoice.place_of_supply, l.gst_rate)
        a = b2c_agg.setdefault(k, [STATE_NAMES.get(k[0], k[0]), k[1], ZERO, ZERO, ZERO, ZERO])
        a[2] += l.taxable; a[3] += l.igst; a[4] += l.cgst; a[5] += l.sgst
    b2c = list(b2c_agg.values())
    hsn_agg = OrderedDict()
    for l in inv_lines:
        a = hsn_agg.setdefault((l.hsn_code, l.gst_rate), [l.hsn_code, l.description, "PKT", ZERO, ZERO, ZERO, ZERO, ZERO, l.gst_rate])
        a[3] += l.qty; a[4] += l.taxable; a[5] += l.igst; a[6] += l.cgst; a[7] += l.sgst
    hsn = [r[:8] + [r[8]] for r in hsn_agg.values()]
    cns = list(CreditNote.objects.filter(date__range=(d1, d2)).select_related("invoice"))
    cdn = [[c.invoice.buyer_gstin or "Unregistered", c.invoice.buyer_name, c.number, c.date, c.invoice.number,
            c.taxable_value, c.igst, c.cgst, c.sgst, c.total, c.reason] for c in cns]
    gross = [sum((l.taxable for l in inv_lines), ZERO), sum((l.igst for l in inv_lines), ZERO),
             sum((l.cgst for l in inv_lines), ZERO), sum((l.sgst for l in inv_lines), ZERO)]
    cn_t = [sum((c.taxable_value for c in cns), ZERO), sum((c.igst for c in cns), ZERO),
            sum((c.cgst for c in cns), ZERO), sum((c.sgst for c in cns), ZERO)]
    net = [g - c for g, c in zip(gross, cn_t)]
    tb = [["Outward taxable supplies - gross (invoices)"] + gross,
          ["Less: credit notes"] + [-x for x in cn_t],
          ["3.1(a) Net outward taxable supplies"] + net]
    return [
        {"name": "GSTR-3B 3.1(a)", "headers": ["Description", "Taxable value", "IGST", "CGST", "SGST"], "rows": tb},
        {"name": "GSTR-1 B2B (4A)", "headers": ["Receiver GSTIN", "Receiver", "Invoice no", "Date", "Place of supply", "Rate %", "Taxable", "IGST", "CGST", "SGST", "Invoice line value"], "rows": b2b},
        {"name": "GSTR-1 B2C (7)", "headers": ["Place of supply", "Rate %", "Taxable", "IGST", "CGST", "SGST"], "rows": b2c},
        {"name": "GSTR-1 CDN (9B)", "headers": ["Receiver GSTIN", "Receiver", "Note no", "Note date", "Original invoice", "Taxable", "IGST", "CGST", "SGST", "Value", "Reason"], "rows": cdn},
        {"name": "GSTR-1 HSN (12)", "headers": ["HSN", "Description", "UQC", "Qty", "Taxable", "IGST", "CGST", "SGST", "Rate %"], "rows": hsn},
    ]


def purchase_register(d1, d2):
    rows = []
    for l in PurchaseLine.objects.filter(purchase__date__range=(d1, d2)).select_related("purchase", "purchase__supplier", "item", "unit").order_by("purchase__date", "purchase_id"):
        p = l.purchase
        rows.append([p.date, p.number, p.supplier.name, p.supplier.gstin, p.bill_no, l.item.name, l.qty, l.unit.code,
                     l.rate, l.taxable, l.gst_rate, l.tax, l.taxable + l.tax])
    return {"name": "Purchase register", "headers": ["Date", "Purchase", "Supplier", "Supplier GSTIN", "Bill no", "Item", "Qty", "Unit", "Rate", "Taxable", "GST %", "GST", "Total"],
            "rows": rows, "totals": _tot(rows, [9, 11, 12], 0, "Total", 13)}


def stock_summary_report(d1, d2):
    rows = [[r["item"].get_kind_display(), r["item"].name, r["item"].base_unit.code, r["opening"], r["receipts"],
             -r["issues"], -r["sales"], r["adjustments"], r["closing"], r["wac"], r["value"]]
            for r in stock_summary(None, d1, d2)]
    return {"name": "Stock summary", "headers": ["Type", "Item", "Unit", "Opening", "Receipts", "Issued to production", "Dispatched", "Adjustments", "Closing", "Avg cost", "Value"], "rows": rows,
            "totals": _tot(rows, [10], 0, "Total value", 11)}


def stock_ledger_report(d1, d2):
    tables = []
    for item in Item.objects.filter(active=True).select_related("base_unit"):
        rows = []
        for r in ledger_rows(item, d1, d2):
            if r.get("opening"):
                rows.append([d1, "", "Opening balance b/f", "", "", "", r["balance"], "", "", "", ""])
                continue
            m = r["m"]
            rows.append([m.date, m.get_movement_type_display(), m.source_ref, m.remarks,
                         m.qty if m.qty > 0 else "", -m.qty if m.qty < 0 else "", r["balance"], m.unit_cost, m.value,
                         m.batch_no, m.created_by.username if m.created_by else ""])
        tables.append({"name": f"Ledger - {item.name} ({item.base_unit.code})",
                       "headers": ["Date", "Movement", "Source document", "Remarks", "In", "Out", "Balance", "Unit cost", "Value", "Batch", "User"], "rows": rows})
    return tables


def production_report(d1, d2):
    runs = ProductionRun.objects.filter(run_date__range=(d1, d2)).select_related("product").prefetch_related("lines__item__base_unit").order_by("run_date", "id")
    summary, lines = [], []
    for r in runs:
        summary.append([r.run_no, r.run_date, r.product.name, r.batch_no, r.expected_output, r.good_output,
                        r.yield_variance_pct, "FLAG" if r.yield_flagged else "ok", r.wastage_packets,
                        r.material_cost, r.total_overhead, r.cost_per_packet, r.margin_per_packet, r.get_status_display()])
        for l in r.lines.all():
            lines.append([r.run_no, l.item.name, l.item.base_unit.code, l.planned_qty, l.actual_qty, l.variance_qty,
                          l.variance_pct, "FLAG" if l.flagged else "ok", l.unit_cost, l.cost])
    return [
        {"name": "Production runs: yield & cost", "headers": ["Run", "Date", "Product", "Batch", "Expected packets", "Good packets", "Yield var %", "Flag", "Wastage (packs)", "Material cost", "Overheads", "Cost / packet", "Margin / packet", "Status"], "rows": summary},
        {"name": "Planned vs actual consumption", "headers": ["Run", "Item", "Unit", "Planned", "Actual", "Variance", "Var %", "Flag", "Rate (WAC)", "Cost"], "rows": lines},
    ]


def collections_report(d1, d2):
    pay = [[p.date, p.number, p.customer.name, p.invoice.number if p.invoice else "On account", p.get_mode_display(), p.reference, p.amount]
           for p in Payment.objects.filter(date__range=(d1, d2)).select_related("customer", "invoice").order_by("date", "id")]
    ag = receivables(d2)
    ar = [[a["customer"].name, a["billed"], a["paid"], a["outstanding"]] + list(a["buckets"].values()) for a in ag if a["outstanding"] != 0]
    inv_rows = [[o["invoice"].number, a["customer"].name, o["invoice"].date, o["age"], o["due"]] for a in ag for o in a["open_invoices"]]
    return [
        {"name": "Payments received", "headers": ["Date", "Receipt", "Customer", "Against", "Mode", "Reference", "Amount"], "rows": pay, "totals": _tot(pay, [6], 0, "Total", 7)},
        {"name": f"Outstanding & ageing as of {d2:%d-%b-%Y}", "headers": ["Customer", "Billed", "Received", "Outstanding", "0-30 days", "31-60", "61-90", "90+"], "rows": ar, "totals": _tot(ar, range(1, 8), 0, "Total", 8)},
        {"name": "Open invoices", "headers": ["Invoice", "Customer", "Date", "Age (days)", "Balance due"], "rows": inv_rows},
    ]


REPORTS = OrderedDict([
    ("sales-register", ("Sales register", "Every invoice and credit note in the period", sales_register)),
    ("customer-sales", ("Customer-wise sales", "Net of cancelled invoices", customer_sales)),
    ("product-sales", ("Product-wise sales", "Quantity and value by product", product_sales)),
    ("gst-summary", ("GST summary (GSTR-1 / 3B)", "Outward supplies, HSN summary and credit notes", gst_summary)),
    ("purchase-register", ("Purchase register", "Raw and packaging purchases", purchase_register)),
    ("stock-summary", ("Stock summary", "Opening + receipts - issues = closing for every item", stock_summary_report)),
    ("stock-ledger", ("Stock ledger", "Every movement with its source document", stock_ledger_report)),
    ("production", ("Production & variance", "Planned vs actual consumption, yield, wastage, cost per packet", production_report)),
    ("collections", ("Collections & outstanding", "Payments, receivables and ageing", collections_report)),
])


def _range(request):
    today = datetime.date.today()
    def parse(v, default):
        try:
            return datetime.date.fromisoformat(v)
        except (TypeError, ValueError):
            return default
    d1 = parse(request.GET.get("from"), today.replace(day=1))
    d2 = parse(request.GET.get("to"), today)
    return (d1, d2) if d1 <= d2 else (d2, d1)


@role_required(*READERS)
def reports_index(request):
    d1, d2 = _range(request)
    return render(request, "reports/index.html", {"reports": [(k, v[0], v[1]) for k, v in REPORTS.items()], "d1": d1, "d2": d2})


@role_required(*READERS)
def report_view(request, slug):
    if slug not in REPORTS:
        raise Http404
    title, _, fn = REPORTS[slug]
    d1, d2 = _range(request)
    built = fn(d1, d2)
    tables = built if isinstance(built, list) else [built]
    report = {"title": f"{title} - {settings.COMPANY['name']}", "subtitle": f"Period: {d1:%d-%b-%Y} to {d2:%d-%b-%Y}", "tables": tables}
    fmt = request.GET.get("format")
    fname = f"{slug}_{d1:%Y%m%d}_{d2:%Y%m%d}"
    if fmt == "csv":
        return exports.to_csv(report, fname)
    if fmt == "xlsx":
        return exports.to_xlsx(report, fname)
    if fmt == "pdf":
        return exports.to_pdf(report, fname)
    return render(request, "reports/report.html", {"report": report, "slug": slug, "title": title, "d1": d1, "d2": d2})
