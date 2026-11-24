import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import AuditLog, User
from accounts.permissions import can, role_required
from inventory.services import on_hand
from masters.models import BOM, Item, Unit, to_base
from masters.utils import D

from .models import ProductionRun
from .services import cancel_run, planned_lines, post_run, update_overheads

R = User.Role
READ = (R.ADMIN, R.PRODUCTION, R.ACCOUNTANT, R.OWNER)
WRITE = (R.ADMIN, R.PRODUCTION)


def _dec(v, default=None):
    v = (v or "").strip()
    if v == "":
        return default
    try:
        return Decimal(v)
    except InvalidOperation:
        raise ValidationError(f"'{v}' is not a number.")


@role_required(*READ)
def production_list(request):
    runs = ProductionRun.objects.select_related("product").prefetch_related("lines")[:100]
    return render(request, "desktop/production_list.html", {"runs": runs, "can_write": can(request.user, *WRITE)})


@role_required(*WRITE)
def production_new(request):
    products = Item.objects.filter(kind="FG", active=True, boms__is_active=True).distinct()
    product = None
    pid = request.POST.get("product") or request.GET.get("product")
    if pid:
        product = products.filter(pk=pid).first()
    elif products.count() == 1:
        product = products.first()
    batches = _safe(request, "batches", "1")
    bom = BOM.objects.filter(product=product, is_active=True).first() if product else None
    lines = []
    if bom:
        for bl, planned in planned_lines(bom, batches or 1):
            lines.append({"bl": bl, "planned": planned, "on_hand": on_hand(bl.item),
                          "val": request.POST.get(f"actual_{bl.item_id}", ""),
                          "units": Unit.objects.filter(dimension=bl.item.base_unit.dimension, is_carton=False)})
    if request.method == "POST" and bom and request.POST.get("action") == "save":
        try:
            actuals = {}
            for l in lines:
                raw = request.POST.get(f"actual_{l['bl'].item_id}")
                q = _dec(raw)
                if q is not None:
                    unit = Unit.objects.get(pk=request.POST.get(f"unit_{l['bl'].item_id}") or l["bl"].item.base_unit_id)
                    actuals[l["bl"].item_id] = to_base(q, unit, l["bl"].item)
            run = post_run(
                request.user, product, _date(request.POST.get("run_date")), _dec(request.POST.get("batches"), Decimal("1")),
                _dec(request.POST.get("good_output"), Decimal("0")), _dec(request.POST.get("wastage"), Decimal("0")), actuals,
                _dec(request.POST.get("labour"), Decimal("0")), _dec(request.POST.get("power_gas"), Decimal("0")),
                _dec(request.POST.get("other"), Decimal("0")), request.POST.get("notes", ""))
            messages.success(request, f"Run {run.run_no} posted: raw stock down, finished goods up.")
            return redirect("production_detail", pk=run.pk)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    return render(request, "desktop/production_form.html", {
        "products": products, "product": product, "bom": bom, "lines": lines, "batches": batches,
        "today": datetime.date.today(), "post": request.POST})


def _safe(request, key, default):
    try:
        return D(request.POST.get(key) or request.GET.get(key) or default)
    except Exception:
        return D(default)


def _date(v):
    try:
        return datetime.date.fromisoformat(v)
    except (TypeError, ValueError):
        return datetime.date.today()


@role_required(*READ)
def production_detail(request, pk):
    run = get_object_or_404(ProductionRun.objects.select_related("product", "bom"), pk=pk)
    if request.method == "POST":
        if not can(request.user, *WRITE):
            messages.error(request, "Read-only role.")
            return redirect("production_detail", pk=pk)
        try:
            if request.POST.get("action") == "overheads":
                update_overheads(request.user, run, _dec(request.POST.get("labour"), Decimal("0")),
                                 _dec(request.POST.get("power_gas"), Decimal("0")), _dec(request.POST.get("other"), Decimal("0")))
                messages.success(request, "Overheads saved; cost per packet updated.")
            elif request.POST.get("action") == "cancel":
                cancel_run(request.user, run, request.POST.get("reason", ""))
                messages.success(request, "Run cancelled; stock movements reversed.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("production_detail", pk=pk)
    logs = AuditLog.objects.filter(entity="productionrun", entity_id=str(run.pk))
    return render(request, "desktop/production_detail.html", {
        "run": run, "lines": run.lines.select_related("item", "item__base_unit"), "logs": logs,
        "can_write": can(request.user, *WRITE)})
