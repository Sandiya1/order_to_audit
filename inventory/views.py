import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from accounts.permissions import can, role_required
from masters.models import Item
from .forms import AdjustmentForm, PurchaseHeaderForm, PurchaseLineFormSet
from .models import Purchase
from .services import adjust_stock, create_purchase, ledger_rows, stock_summary

R = User.Role
READ = (R.ADMIN, R.PRODUCTION, R.ACCOUNTANT, R.OWNER)
WRITE = (R.ADMIN, R.PRODUCTION)


def _dates(request):
    def p(v):
        try:
            return datetime.date.fromisoformat(v)
        except (TypeError, ValueError):
            return None
    return p(request.GET.get("from")), p(request.GET.get("to"))


@role_required(*READ)
def stock_list(request):
    d1, d2 = _dates(request)
    rows = stock_summary(None, d1, d2)
    groups = [(label, [r for r in rows if r["item"].kind == k]) for k, label in Item.Kind.choices]
    return render(request, "desktop/stock_summary.html", {"groups": groups, "d1": d1, "d2": d2})


@role_required(*READ)
def stock_ledger(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    d1, d2 = _dates(request)
    return render(request, "desktop/stock_ledger.html", {"item": item, "rows": ledger_rows(item, d1, d2), "d1": d1, "d2": d2,
                                                         "items": Item.objects.filter(active=True)})


@role_required(*WRITE)
def stock_adjust(request):
    form = AdjustmentForm(request.POST or None, initial={"date": datetime.date.today()})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            adjust_stock(cd["item"], cd["delta"], cd["date"], request.user, cd["reason"])
            messages.success(request, "Adjustment posted to the stock ledger.")
            return redirect("stock_ledger", item_id=cd["item"].pk)
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "desktop/form.html", {"form": form, "title": "Stock adjustment"})


@role_required(*READ)
def purchase_list(request):
    return render(request, "desktop/purchase_list.html",
                  {"purchases": Purchase.objects.select_related("supplier").prefetch_related("lines__item", "lines__unit")[:100],
                   "can_write": can(request.user, *WRITE)})


@role_required(*WRITE)
def purchase_new(request):
    header = PurchaseHeaderForm(request.POST or None, initial={"date": datetime.date.today()})
    fs = PurchaseLineFormSet(request.POST or None)
    if request.method == "POST" and header.is_valid() and fs.is_valid():
        lines = [f.cleaned_data for f in fs if f.cleaned_data.get("item") and f.cleaned_data.get("qty")]
        try:
            for ln in lines:
                if not ln.get("unit") or ln.get("rate") is None:
                    raise ValidationError("Each line needs a unit and a rate.")
            p = create_purchase(request.user, header.cleaned_data["supplier"], header.cleaned_data["date"], lines,
                                header.cleaned_data["bill_no"], header.cleaned_data["notes"])
            messages.success(request, f"Purchase {p.number} recorded; raw stock updated.")
            return redirect("purchase_list")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    return render(request, "desktop/purchase_form.html", {"header": header, "formset": fs})
