"""Production engine: BOM -> planned consumption, actuals, stock postings, costing."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.audit import audit
from accounts.models import Sequence
from inventory.models import StockMovement
from inventory.services import InsufficientStock, current_wac, on_hand, post_movement
from masters.models import BOM, Item
from masters.utils import D, cost4, qty3

from .models import ProductionRun, ProductionRunLine

ZERO = Decimal("0")
T = StockMovement.Type


def planned_lines(bom, batches):
    """[(BOMLine, planned qty in base unit)] for the given number of batches."""
    return [(bl, qty3(bl.qty_base * D(batches))) for bl in bom.lines.select_related("item", "unit", "item__base_unit")]


@transaction.atomic
def post_run(user, product, run_date, batches, good_output, wastage_packets=0, actuals=None,
             labour=0, power_gas=0, other=0, notes=""):
    """Record a production run and post stock movements.

    actuals: {item_id: qty in base unit} actually issued. Anything omitted defaults to
    the BOM plan, except the primary pack (pouch) which defaults to good output + damaged.
    """
    actuals = actuals or {}
    bom = BOM.objects.filter(product=product, is_active=True).first()
    if not bom:
        raise ValidationError(f"{product.name} has no active BOM.")
    batches, good_output, wastage_packets = D(batches), D(good_output), D(wastage_packets)
    if batches <= 0 or good_output < 0 or wastage_packets < 0:
        raise ValidationError("Batches must be positive; output and wastage cannot be negative.")
    expected = qty3(bom.yield_qty * batches)

    plan = []
    for bl, planned in planned_lines(bom, batches):
        if bl.item_id in actuals and actuals[bl.item_id] is not None:
            actual = qty3(actuals[bl.item_id])
        elif bl.is_primary_pack:
            actual = qty3(good_output + wastage_packets)
        else:
            actual = planned
        if actual < 0:
            raise ValidationError(f"Negative quantity for {bl.item.name}.")
        plan.append((bl, planned, actual))

    shortages = []
    for bl, planned, actual in plan:
        have = on_hand(bl.item)
        if actual > have:
            shortages.append(f"{bl.item.name}: need {actual:f} {bl.item.base_unit.code}, have {have:f}")
    if shortages:
        raise InsufficientStock("Not enough raw material - " + "; ".join(shortages))

    seq = Sequence.next("RUN")
    run = ProductionRun.objects.create(
        run_no=f"PR-{seq:04d}", product=product, bom=bom, run_date=run_date, batches=batches,
        expected_output=expected, good_output=good_output, wastage_packets=wastage_packets,
        tolerance_pct=bom.tolerance_pct, notes=notes,
        batch_no=f"{product.code}-{run_date:%y%m%d}-{seq:02d}",
        labour_cost=D(labour), power_gas_cost=D(power_gas), other_overhead=D(other), created_by=user)

    material = ZERO
    for bl, planned, actual in plan:
        wac = current_wac(bl.item)
        mv = None
        if actual > 0:
            mv = post_movement(bl.item, -actual, T.PRODUCTION_ISSUE, "production_run", run.pk, run.run_no,
                               run_date, user, unit_cost=wac, batch_no=run.batch_no)
        cost = -mv.value if mv else ZERO
        material += cost
        ProductionRunLine.objects.create(run=run, item=bl.item, planned_qty=planned, actual_qty=actual,
                                         unit_cost=wac, cost=cost)
    run.material_cost = material
    expiry = (run_date + datetime.timedelta(days=product.shelf_life_days)) if product.shelf_life_days else None
    overhead = run.total_overhead
    if good_output > 0:
        post_movement(product, good_output, T.PRODUCTION_OUTPUT, "production_run", run.pk, run.run_no, run_date,
                      user, unit_cost=(material + overhead) / good_output, batch_no=run.batch_no,
                      expiry_date=expiry, value=cost4(material + overhead))
        run.overhead_capitalised = overhead
    run.save()
    audit(user, run, "create", f"{product.name}: expected {expected:f}, good {good_output:f}, "
                               f"wastage {wastage_packets:f}, material cost {material:f}")
    return run


@transaction.atomic
def update_overheads(user, run, labour, power_gas, other):
    """Overheads are often known after the run. Changing them is audited and the difference
    is capitalised into finished-goods value with a quantity-less COST_ADJ ledger row."""
    if run.status != ProductionRun.Status.POSTED:
        raise ValidationError("Cancelled runs cannot be edited.")
    old = (run.labour_cost, run.power_gas_cost, run.other_overhead)
    run.labour_cost, run.power_gas_cost, run.other_overhead = D(labour), D(power_gas), D(other)
    delta = run.total_overhead - run.overhead_capitalised
    if delta != 0 and run.good_output > 0:
        post_movement(run.product, 0, T.COST_ADJUST, "production_run", run.pk, run.run_no, datetime.date.today(),
                      user, unit_cost=0, value=cost4(delta), batch_no=run.batch_no,
                      remarks="Overhead allocation")
        run.overhead_capitalised = run.total_overhead
    run.save()
    audit(user, run, "edit_overheads",
          f"labour {old[0]}->{run.labour_cost}, power/gas {old[1]}->{run.power_gas_cost}, other {old[2]}->{run.other_overhead}")
    return run


@transaction.atomic
def cancel_run(user, run, reason):
    """Reverse a run: raw material goes back, finished goods come out (if still in stock)."""
    if run.status == ProductionRun.Status.CANCELLED:
        raise ValidationError("Run already cancelled.")
    if not reason.strip():
        raise ValidationError("A reason is required.")
    for mv in StockMovement.objects.filter(source_type="production_run", source_id=run.pk):
        if mv.movement_type == T.COST_ADJUST:
            post_movement(mv.item, 0, T.REVERSAL, "production_run", run.pk, run.run_no, datetime.date.today(),
                          user, value=-mv.value, remarks=f"Cancel {run.run_no}")
        elif mv.qty != 0:
            post_movement(mv.item, -mv.qty, T.REVERSAL, "production_run", run.pk, run.run_no,
                          datetime.date.today(), user, unit_cost=mv.unit_cost, value=-mv.value,
                          remarks=f"Cancel {run.run_no}: {reason}")
    run.status = ProductionRun.Status.CANCELLED
    run.save()
    audit(user, run, "cancel", reason)
    return run
