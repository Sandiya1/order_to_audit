"""Stock engine. All stock changes go through post_movement()."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When

from accounts.audit import audit
from masters.models import Item, Unit, to_base
from masters.utils import D, cost4, qty3

from .models import Purchase, PurchaseLine, StockMovement

ZERO = Decimal("0")


class InsufficientStock(ValidationError):
    """Raised when an issue/dispatch would take stock below zero."""


def on_hand(item, as_of=None):
    qs = StockMovement.objects.filter(item=item)
    if as_of:
        qs = qs.filter(date__lte=as_of)
    return qs.aggregate(q=Sum("qty"))["q"] or ZERO


def stock_value(item):
    return StockMovement.objects.filter(item=item).aggregate(v=Sum("value"))["v"] or ZERO


def current_wac(item):
    """Weighted-average cost per base unit, derived from the ledger:
    total value on hand / total quantity on hand."""
    agg = StockMovement.objects.filter(item=item).aggregate(q=Sum("qty"), v=Sum("value"))
    q, v = agg["q"] or ZERO, agg["v"] or ZERO
    if q > 0:
        return cost4(v / q)
    last = (StockMovement.objects.filter(item=item, qty__gt=0).order_by("-date", "-id").first())
    return last.unit_cost if last else ZERO


@transaction.atomic
def post_movement(item, qty, movement_type, source_type, source_id=0, source_ref="", date=None, user=None,
                  unit_cost=None, batch_no="", expiry_date=None, remarks="", allow_negative=False, value=None):
    """Append one row to the ledger.

    * Receipts (qty > 0) are valued at `unit_cost` (purchase rate, production cost...).
    * Issues (qty < 0) are valued at the item's weighted-average cost at that moment,
      unless `unit_cost` is given (used when returning goods at their original cost).
    """
    qty = qty3(qty)
    Item.objects.select_for_update().get(pk=item.pk)  # serialise per item on real DBs
    if qty < 0 and not allow_negative:
        have = on_hand(item)
        if have + qty < 0:
            raise InsufficientStock(
                f"Insufficient {item.name}: need {abs(qty):f} {item.base_unit.code}, have {have:f}.")
    if unit_cost is None:
        unit_cost = current_wac(item) if qty < 0 else ZERO
    unit_cost = cost4(unit_cost)
    if value is None:
        value = cost4(qty * unit_cost)
    return StockMovement.objects.create(
        item=item, date=date or datetime.date.today(), movement_type=movement_type, qty=qty,
        unit_cost=unit_cost, value=value, source_type=source_type, source_id=source_id,
        source_ref=source_ref, batch_no=batch_no, expiry_date=expiry_date, remarks=remarks, created_by=user)


@transaction.atomic
def adjust_stock(item, delta, date, user, reason):
    """Manual stock adjustment (physical count difference, spoilage...)."""
    if not reason.strip():
        raise ValidationError("A reason is required for stock adjustments.")
    mv = post_movement(item, delta, StockMovement.Type.ADJUSTMENT, "adjustment", 0, "ADJ", date, user,
                       remarks=reason)
    audit(user, item, "stock_adjustment", f"{delta:+f} {item.base_unit.code}: {reason}", reference=item.code)
    return mv


@transaction.atomic
def create_purchase(user, supplier, date, lines, bill_no="", notes=""):
    """lines: iterable of dicts(item, qty, unit, rate, gst_rate, batch_no, expiry_date)."""
    purchase = Purchase.objects.create(supplier=supplier, bill_no=bill_no, date=date, notes=notes, created_by=user)
    n = 0
    for ln in lines:
        item, unit = ln["item"], ln["unit"]
        qty_base = qty3(to_base(ln["qty"], unit, item))
        if qty_base <= 0:
            raise ValidationError("Purchase quantity must be positive.")
        pl = PurchaseLine.objects.create(
            purchase=purchase, item=item, qty=ln["qty"], unit=unit, rate=ln["rate"],
            gst_rate=ln.get("gst_rate") or 0, qty_base=qty_base,
            batch_no=ln.get("batch_no", ""), expiry_date=ln.get("expiry_date"))
        base_cost = D(ln["rate"]) * D(ln["qty"]) / qty_base       # ex-GST cost per base unit
        post_movement(item, qty_base, StockMovement.Type.PURCHASE, "purchase", purchase.pk, purchase.number,
                      date, user, unit_cost=base_cost, batch_no=pl.batch_no, expiry_date=pl.expiry_date,
                      remarks=f"{supplier.name} bill {bill_no}".strip())
        n += 1
    if n == 0:
        raise ValidationError("A purchase needs at least one line.")
    audit(user, purchase, "create", f"{n} line(s) from {supplier.name}")
    return purchase


# ---------------------------------------------------------------- reporting helpers
def _sum_if(cond):
    return Sum(Case(When(cond, then=F("qty")), default=Value(ZERO), output_field=DecimalField()))


def stock_summary(kinds=None, date_from=None, date_to=None):
    """Per item: opening, receipts, production issues, sales, adjustments, closing (+ value)."""
    date_to = date_to or datetime.date.today()
    items = Item.objects.filter(active=True).select_related("base_unit")
    if kinds:
        items = items.filter(kind__in=kinds)
    T = StockMovement.Type
    rows = []
    for item in items:
        mv = StockMovement.objects.filter(item=item, date__lte=date_to)
        before = Q(date__lt=date_from) | Q(movement_type=T.OPENING) if date_from else Q(movement_type=T.OPENING)
        inside = Q(date__gte=date_from) if date_from else Q()
        agg = mv.aggregate(
            opening=_sum_if(before),
            receipts=_sum_if(inside & ~Q(movement_type=T.OPENING) & Q(movement_type__in=[
                T.PURCHASE, T.PRODUCTION_OUTPUT, T.SALE_RETURN])),
            issues=_sum_if(inside & ~Q(movement_type=T.OPENING) & Q(movement_type=T.PRODUCTION_ISSUE)),
            sales=_sum_if(inside & ~Q(movement_type=T.OPENING) & Q(movement_type=T.SALE)),
            adjustments=_sum_if(inside & ~Q(movement_type=T.OPENING) & Q(movement_type__in=[
                T.ADJUSTMENT, T.REVERSAL])),
            closing=Sum("qty"), value=Sum("value"))
        agg = {k: (v or ZERO) for k, v in agg.items()}
        # opening movements dated inside the window still count as opening, so fix up "before"
        rows.append({"item": item, **agg,
                     "wac": (agg["value"] / agg["closing"]) if agg["closing"] > 0 else ZERO,
                     "low": item.reorder_level > 0 and agg["closing"] <= item.reorder_level})
    return rows


def ledger_rows(item, date_from=None, date_to=None):
    """Ledger lines with running balance (balance includes everything before date_from)."""
    qs = StockMovement.objects.filter(item=item).select_related("created_by")
    running = ZERO
    if date_from:
        running = qs.filter(date__lt=date_from).aggregate(q=Sum("qty"))["q"] or ZERO
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    out = [{"opening": True, "balance": running}] if date_from else []
    for m in qs:
        running += m.qty
        out.append({"m": m, "balance": running})
    return out
