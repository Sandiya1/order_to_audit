"""Sales loop: order -> verify -> GST invoice -> dispatch -> payment, plus credit notes."""
import datetime
from collections import OrderedDict
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounts.audit import audit
from accounts.models import Sequence
from inventory.models import StockMovement
from inventory.services import InsufficientStock, on_hand, post_movement
from masters.models import Customer, Item, price_for
from masters.utils import D, fy_label, money, qty3

from .models import (CreditNote, Dispatch, Invoice, InvoiceLine, Order, OrderLine, Payment)
from .tax import invoice_totals, is_interstate, line_tax

ZERO = Decimal("0")
T = StockMovement.Type
S = Order.Status


class ShortageError(ValidationError):
    """Approval blocked because finished-goods stock does not cover the order."""
    def __init__(self, shortages):
        self.shortages = shortages
        msg = "; ".join(f"{s['item'].name}: short by {s['shortage']:f}" for s in shortages)
        super().__init__(f"Insufficient stock - {msg}")


# ------------------------------------------------------------------ orders
@transaction.atomic
def place_order(user, customer, lines, client_uuid=None, notes=""):
    """lines: iterable of (item, qty). Returns (order, created). Idempotent on client_uuid."""
    if client_uuid:
        existing = Order.objects.filter(client_uuid=client_uuid).first()
        if existing:
            return existing, False
    merged = OrderedDict()
    for item, qty in lines:
        qty = qty3(qty)
        if qty <= 0:
            continue
        if item.kind != Item.Kind.FG or not item.active:
            raise ValidationError(f"{item.name} cannot be ordered.")
        merged[item.pk] = (item, merged.get(item.pk, (item, ZERO))[1] + qty)
    if not merged:
        raise ValidationError("Add at least one product with a quantity.")
    order = Order.objects.create(number=f"SO-{Sequence.next('ORDER'):04d}", customer=customer, salesman=user,
                                 client_uuid=client_uuid, notes=notes)
    for item, qty in merged.values():
        OrderLine.objects.create(order=order, item=item, qty=qty, rate=price_for(customer, item))
    audit(user, order, "create", f"{customer.name}: {len(merged)} line(s)")
    return order, True


def reserved_qty(item, exclude_order=None):
    """Stock promised to approved/invoiced orders that have not left the factory yet."""
    qs = OrderLine.objects.filter(item=item, order__status__in=[S.APPROVED, S.INVOICED])
    if exclude_order:
        qs = qs.exclude(order=exclude_order)
    return qs.aggregate(q=Sum("qty"))["q"] or ZERO


def stock_check(order):
    rows = []
    for ln in order.lines.select_related("item", "item__base_unit"):
        oh, res = on_hand(ln.item), reserved_qty(ln.item, exclude_order=order)
        avail = oh - res
        rows.append({"line": ln, "item": ln.item, "requested": ln.qty, "on_hand": oh, "reserved": res,
                     "available": avail, "shortage": max(ln.qty - max(avail, ZERO), ZERO)})
    return rows


def tax_preview(order):
    pos = order.customer.state_code
    igst = is_interstate(settings.COMPANY["state_code"], pos)
    lines = []
    for ln in order.lines.select_related("item"):
        taxable = ln.amount
        lines.append({"taxable": taxable, **line_tax(taxable, ln.item.gst_rate, igst)})
    return {"igst_applies": igst, "place_of_supply": pos, **invoice_totals(lines)}


@transaction.atomic
def update_order(user, order, edits=None, remove=()):
    """Admin edits before approval. edits: {line_id: (qty, rate)}."""
    if order.status != S.SUBMITTED:
        raise ValidationError("Only submitted orders can be edited.")
    changes = []
    for ln in order.lines.all():
        if ln.pk in remove:
            changes.append(f"removed {ln.item.name}")
            ln.delete()
        elif edits and ln.pk in edits:
            qty, rate = qty3(edits[ln.pk][0]), money(edits[ln.pk][1])
            if qty <= 0:
                raise ValidationError("Quantity must be positive (use remove to drop a line).")
            if (qty, rate) != (ln.qty, ln.rate):
                changes.append(f"{ln.item.name}: qty {ln.qty:f}->{qty:f}, rate {ln.rate}->{rate}")
                ln.qty, ln.rate = qty, rate
                ln.save()
    if not order.lines.exists():
        raise ValidationError("An order needs at least one line; reject it instead.")
    if changes:
        audit(user, order, "edit", "; ".join(changes))
    return order


@transaction.atomic
def reduce_to_available(user, order):
    """Trim each line to what stock can cover (partial fulfilment)."""
    changes = []
    for row in stock_check(order):
        if row["shortage"] > 0:
            ln, new = row["line"], max(row["available"], ZERO)
            changes.append(f"{ln.item.name}: {ln.qty:f}->{new:f}")
            if new > 0:
                ln.qty = new
                ln.save()
            else:
                ln.delete()
    if not order.lines.exists():
        raise ValidationError("No stock available for any line; reject the order instead.")
    if changes:
        audit(user, order, "edit", "reduced to available stock: " + "; ".join(changes))
    return order


@transaction.atomic
def approve_order(user, order):
    if order.status != S.SUBMITTED:
        raise ValidationError("Only submitted orders can be approved.")
    short = [r for r in stock_check(order) if r["shortage"] > 0]
    if short:
        raise ShortageError(short)
    order.status, order.decided_by = S.APPROVED, user
    order.save()
    audit(user, order, "approve")
    return order


@transaction.atomic
def reject_order(user, order, reason=""):
    if order.status != S.SUBMITTED:
        raise ValidationError("Only submitted orders can be rejected.")
    order.status, order.decided_by, order.reject_reason = S.REJECTED, user, reason
    order.save()
    audit(user, order, "reject", reason)
    return order


# ------------------------------------------------------------------ invoice
@transaction.atomic
def create_invoice(user, order, date=None):
    if order.status != S.APPROVED:
        raise ValidationError("Only approved orders can be invoiced.")
    date = date or datetime.date.today()
    cust = order.customer
    igst = is_interstate(settings.COMPANY["state_code"], cust.state_code)
    fy = fy_label(date)
    number = f"INV/{fy}/{Sequence.next('INV/' + fy):04d}"

    rows = []
    for ln in order.lines.select_related("item"):
        taxable = ln.amount
        rows.append((ln, {"taxable": taxable, **line_tax(taxable, ln.item.gst_rate, igst)}))
    totals = invoice_totals([r for _, r in rows])
    inv = Invoice.objects.create(
        number=number, fy=fy, order=order, date=date, customer=cust, buyer_name=cust.name,
        buyer_gstin=cust.gstin, buyer_address=cust.address, place_of_supply=cust.state_code, is_igst=igst,
        taxable_value=totals["taxable"], cgst=totals["cgst"], sgst=totals["sgst"], igst=totals["igst"],
        total=totals["total"], created_by=user)
    for ln, r in rows:
        InvoiceLine.objects.create(invoice=inv, item=ln.item, description=ln.item.name, hsn_code=ln.item.hsn_code,
                                   qty=ln.qty, rate=ln.rate, taxable=r["taxable"], gst_rate=ln.item.gst_rate,
                                   cgst=r["cgst"], sgst=r["sgst"], igst=r["igst"])
    order.status = S.INVOICED
    order.save()
    audit(user, inv, "create", f"total {inv.total} ({'IGST' if igst else 'CGST+SGST'})")
    return inv


@transaction.atomic
def dispatch_invoice(user, invoice, date=None, vehicle_no="", remarks=""):
    if invoice.status != Invoice.Status.ISSUED:
        raise ValidationError("Cancelled invoices cannot be dispatched.")
    if invoice.is_dispatched:
        raise ValidationError("Already dispatched.")
    date = date or datetime.date.today()
    number = f"DSP-{Sequence.next('DISPATCH'):04d}"
    short = []
    for ln in invoice.lines.select_related("item"):
        have = on_hand(ln.item)
        if have < ln.qty:
            short.append(f"{ln.item.name}: need {ln.qty:f}, have {have:f}")
    if short:
        raise InsufficientStock("Cannot dispatch - " + "; ".join(short))
    d = Dispatch.objects.create(number=number, invoice=invoice, date=date, vehicle_no=vehicle_no,
                                remarks=remarks, created_by=user)
    for ln in invoice.lines.select_related("item"):
        post_movement(ln.item, -ln.qty, T.SALE, "invoice", invoice.pk, invoice.number, date, user,
                      remarks=f"{invoice.buyer_name} ({number})")
    invoice.order.status = S.DISPATCHED
    invoice.order.save()
    audit(user, invoice, "dispatch", number)
    return d


@transaction.atomic
def cancel_invoice(user, invoice, reason):
    """Cancel by credit note; never delete. Dispatched stock is returned at its original cost."""
    if invoice.status == Invoice.Status.CANCELLED:
        raise ValidationError("Invoice already cancelled.")
    if not reason.strip():
        raise ValidationError("A reason is required to cancel an invoice.")
    today = datetime.date.today()
    fy = fy_label(today)
    cn = CreditNote.objects.create(
        number=f"CN/{fy}/{Sequence.next('CN/' + fy):04d}", invoice=invoice, date=today, reason=reason,
        taxable_value=invoice.taxable_value, cgst=invoice.cgst, sgst=invoice.sgst, igst=invoice.igst,
        total=invoice.total, created_by=user)
    if invoice.is_dispatched:
        for mv in StockMovement.objects.filter(source_type="invoice", source_id=invoice.pk, movement_type=T.SALE):
            post_movement(mv.item, -mv.qty, T.SALE_RETURN, "credit_note", cn.pk, cn.number, today, user,
                          unit_cost=mv.unit_cost, value=-mv.value, remarks=f"Cancelled {invoice.number}")
    invoice.status = Invoice.Status.CANCELLED
    invoice.save()
    invoice.order.status = S.CANCELLED
    invoice.order.save()
    audit(user, invoice, "cancel", f"credit note {cn.number}: {reason}")
    return cn


# ------------------------------------------------------------------ payments & receivables
@transaction.atomic
def record_payment(user, customer, amount, date=None, mode="UPI", reference="", invoice=None):
    amount = money(amount)
    if amount <= 0:
        raise ValidationError("Payment must be positive.")
    if invoice and invoice.customer_id != customer.pk:
        raise ValidationError("Invoice belongs to a different customer.")
    p = Payment.objects.create(number=f"RCT-{Sequence.next('RECEIPT'):04d}", customer=customer, invoice=invoice,
                               date=date or datetime.date.today(), amount=amount, mode=mode,
                               reference=reference, created_by=user)
    audit(user, p, "create", f"{customer.name} {amount}" + (f" against {invoice.number}" if invoice else ""))
    return p


BUCKETS = [("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6)]


def receivables(as_of=None, customer=None):
    """Outstanding by customer with ageing. Payments (explicitly allocated or not) are
    applied oldest-invoice-first, which is how most small distributors reconcile."""
    as_of = as_of or datetime.date.today()
    customers = Customer.objects.filter(pk=customer.pk) if customer else Customer.objects.all()
    out = []
    for c in customers:
        invs = [i for i in c.invoices.select_related("credit_note").order_by("date", "id")
                if i.date <= as_of and not (i.status == Invoice.Status.CANCELLED and
                                            hasattr(i, "credit_note") and i.credit_note.date <= as_of)]
        paid = c.payments.filter(date__lte=as_of).aggregate(s=Sum("amount"))["s"] or ZERO
        billed = sum((i.total for i in invs), ZERO)
        if not invs and not paid:
            continue
        pool = paid
        buckets = OrderedDict((b[0], ZERO) for b in BUCKETS)
        open_rows = []
        for i in invs:
            applied = min(pool, i.total)
            pool -= applied
            due = i.total - applied
            if due > 0:
                age = (as_of - i.date).days
                for name, lo, hi in BUCKETS:
                    if lo <= age <= hi:
                        buckets[name] += due
                open_rows.append({"invoice": i, "due": due, "age": age})
        out.append({"customer": c, "billed": billed, "paid": paid, "outstanding": billed - paid,
                    "buckets": buckets, "open_invoices": open_rows})
    return out


def customer_outstanding(customer, as_of=None):
    rows = receivables(as_of, customer)
    return rows[0]["outstanding"] if rows else ZERO
