import datetime
import json
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.models import AuditLog, User
from accounts.permissions import can, role_required
from inventory.services import on_hand, stock_summary
from masters.models import Customer, CustomerPrice, Item
from masters.utils import D, money
from production.models import ProductionRun

from . import services as svc
from .models import Invoice, Order, Payment
from .pdf import build_invoice_pdf

R = User.Role
ZERO = Decimal("0")
DESK = (R.ADMIN,)
DESK_READ = (R.ADMIN, R.ACCOUNTANT, R.OWNER)


# =============================================================== dashboard
@role_required(R.ADMIN, R.OWNER)
def dashboard(request):
    today = datetime.date.today()
    todays = Order.objects.filter(created_at__date=today).exclude(status="REJECTED")
    t_orders = [(o, o.taxable_total) for o in todays.prefetch_related("lines")]
    stock = [r for r in stock_summary(None, None, today)]
    runs = ProductionRun.objects.filter(status="POSTED").select_related("product").prefetch_related("lines")[:20]
    alerts = [r for r in runs if r.has_alert][:6]
    latest_cost = {}
    for r in runs:
        latest_cost.setdefault(r.product_id, r)
    recv = svc.receivables(today)
    due = sorted([a for a in recv if a["outstanding"] > 0], key=lambda a: -a["outstanding"])
    return render(request, "desktop/dashboard.html", {
        "orders_today": t_orders, "orders_today_value": sum((v for _, v in t_orders), ZERO),
        "pending": Order.objects.filter(status="SUBMITTED").count(),
        "stock": stock, "low": [r for r in stock if r["low"]], "alerts": alerts, "latest_cost": latest_cost.values(),
        "total_due": sum((a["outstanding"] for a in due), ZERO), "due": due[:5]})


# =============================================================== admin desk: orders
@role_required(*DESK, R.OWNER, R.ACCOUNTANT)
def order_list(request):
    status = request.GET.get("status", "")
    qs = Order.objects.select_related("customer", "salesman").prefetch_related("lines")
    if status:
        qs = qs.filter(status=status)
    return render(request, "desktop/order_list.html", {"orders": qs[:200], "status": status, "statuses": Order.Status.choices})


@role_required(*DESK_READ)
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("customer", "salesman"), pk=pk)
    is_admin = can(request.user, *DESK)
    if request.method == "POST":
        if not is_admin:
            messages.error(request, "Read-only role.")
            return redirect("order_detail", pk=pk)
        action = request.POST.get("action")
        try:
            if action in ("save", "approve"):
                edits = {}
                for ln in order.lines.all():
                    q, r = request.POST.get(f"qty_{ln.pk}"), request.POST.get(f"rate_{ln.pk}")
                    if q not in (None, "") and r not in (None, ""):
                        edits[ln.pk] = (D(q), D(r))
                remove = {int(x) for x in request.POST.getlist("remove")}
                if order.status == "SUBMITTED":
                    svc.update_order(request.user, order, edits, remove)
                if action == "approve":
                    svc.approve_order(request.user, order)
                    messages.success(request, "Order approved.")
                else:
                    messages.success(request, "Order updated.")
            elif action == "reduce":
                svc.reduce_to_available(request.user, order)
                messages.success(request, "Quantities reduced to available stock.")
            elif action == "reject":
                svc.reject_order(request.user, order, request.POST.get("reason", ""))
                messages.success(request, "Order rejected.")
            elif action == "invoice":
                inv = svc.create_invoice(request.user, order)
                messages.success(request, f"Invoice {inv.number} raised.")
                return redirect("invoice_detail", pk=inv.pk)
        except svc.ShortageError as e:
            messages.error(request, "Cannot approve: " + str(e.messages[0]) + ". Reduce the quantity to available stock or reject.")
        except (ValidationError, InvalidOperation) as e:
            messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
        return redirect("order_detail", pk=pk)
    check = svc.stock_check(order)
    return render(request, "desktop/order_detail.html", {
        "order": order, "check": check, "tax": svc.tax_preview(order), "is_admin": is_admin,
        "shortage": any(r["shortage"] > 0 for r in check),
        "invoice": getattr(order, "invoice", None),
        "logs": AuditLog.objects.filter(entity="order", entity_id=str(order.pk))})


# =============================================================== invoices & payments
@role_required(*DESK_READ)
def invoice_list(request):
    return render(request, "desktop/invoice_list.html", {"invoices": Invoice.objects.select_related("customer", "order")[:200]})


@role_required(*DESK_READ)
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer", "order"), pk=pk)
    is_admin = can(request.user, *DESK)
    if request.method == "POST":
        if not is_admin:
            messages.error(request, "Read-only role.")
            return redirect("invoice_detail", pk=pk)
        try:
            if request.POST.get("action") == "dispatch":
                svc.dispatch_invoice(request.user, inv, vehicle_no=request.POST.get("vehicle_no", ""))
                messages.success(request, "Dispatch recorded; finished-goods stock reduced.")
            elif request.POST.get("action") == "cancel":
                cn = svc.cancel_invoice(request.user, inv, request.POST.get("reason", ""))
                messages.success(request, f"Invoice cancelled with credit note {cn.number}.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("invoice_detail", pk=pk)
    pdf_url = request.build_absolute_uri(reverse("invoice_public_pdf", args=[inv.share_token]))
    from urllib.parse import quote
    text = f"Invoice {inv.number} for Rs {inv.total} from {settings.COMPANY['name']}: {pdf_url}"
    phone = "".join(ch for ch in (inv.customer.phone or "") if ch.isdigit())
    wa = f"https://wa.me/{phone}?text={quote(text)}" if phone else f"https://wa.me/?text={quote(text)}"
    return render(request, "desktop/invoice_detail.html", {
        "inv": inv, "lines": inv.lines.all(), "payments": inv.payments.all(), "is_admin": is_admin,
        "wa": wa, "mail": f"mailto:?subject={quote('Invoice ' + inv.number)}&body={quote(text)}", "pdf_url": pdf_url,
        "logs": AuditLog.objects.filter(entity="invoice", entity_id=str(inv.pk))})


@role_required(*DESK_READ)
def invoice_pdf(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    resp = HttpResponse(build_invoice_pdf(inv), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{inv.number.replace("/", "-")}.pdf"'
    return resp


def invoice_public_pdf(request, token):
    """Unguessable link used by the WhatsApp/email share - the customer has no login."""
    inv = get_object_or_404(Invoice, share_token=token)
    resp = HttpResponse(build_invoice_pdf(inv), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{inv.number.replace("/", "-")}.pdf"'
    return resp


@role_required(*DESK)
def payment_new(request):
    customers = Customer.objects.filter(active=True)
    inv = Invoice.objects.filter(pk=request.GET.get("invoice")).first()
    if request.method == "POST":
        try:
            customer = get_object_or_404(Customer, pk=request.POST.get("customer"))
            invoice = Invoice.objects.filter(pk=request.POST.get("invoice") or None).first()
            p = svc.record_payment(request.user, customer, D(request.POST.get("amount")),
                                   _date(request.POST.get("date")), request.POST.get("mode", "UPI"),
                                   request.POST.get("reference", ""), invoice)
            messages.success(request, f"Payment {p.number} recorded. Outstanding for {customer.name}: ₹{svc.customer_outstanding(customer)}")
            return redirect("outstanding")
        except (ValidationError, InvalidOperation) as e:
            messages.error(request, "; ".join(getattr(e, "messages", ["Enter a valid amount."])))
    open_invoices = Invoice.objects.filter(status="ISSUED").select_related("customer")
    return render(request, "desktop/payment_form.html", {
        "customers": customers, "inv": inv, "open_invoices": open_invoices, "modes": Payment.Mode.choices,
        "today": datetime.date.today()})


def _date(v):
    try:
        return datetime.date.fromisoformat(v)
    except (TypeError, ValueError):
        return datetime.date.today()


@role_required(*DESK_READ)
def outstanding(request):
    rows = [r for r in svc.receivables() if r["outstanding"] != 0]
    return render(request, "desktop/outstanding.html", {
        "rows": rows, "total": sum((r["outstanding"] for r in rows), ZERO),
        "payments": Payment.objects.select_related("customer", "invoice")[:15], "is_admin": can(request.user, *DESK)})


@role_required(R.ADMIN, R.ACCOUNTANT, R.OWNER)
def audit_log(request):
    return render(request, "desktop/audit.html", {"logs": AuditLog.objects.select_related("user")[:300]})


# =============================================================== mobile (salesman)
MOBILE = (R.SALESMAN, R.ADMIN)


def _my_customers(user):
    qs = Customer.objects.filter(active=True)
    mine = qs.filter(salesman=user)
    return mine if mine.exists() else qs


@role_required(*MOBILE)
def m_home(request):
    mine = Order.objects.filter(salesman=request.user)
    today = datetime.date.today()
    return render(request, "m/home.html", {
        "today_count": mine.filter(created_at__date=today).count(),
        "open_count": mine.filter(status__in=["SUBMITTED", "APPROVED", "INVOICED"]).count(),
        "recent": mine.select_related("customer").prefetch_related("lines")[:5]})


@role_required(*MOBILE)
@ensure_csrf_cookie
def m_new_order(request):
    customers = list(_my_customers(request.user))
    items = list(Item.objects.filter(kind="FG", active=True, selling_price__isnull=False))
    base = {i.pk: str(i.selling_price) for i in items}
    special = {}
    for cp in CustomerPrice.objects.filter(customer__in=customers):
        special.setdefault(cp.customer_id, {})[cp.item_id] = str(cp.price)
    catalog = {
        "customers": [{"id": c.pk, "name": c.name, "type": c.get_customer_type_display(), "state": c.state_name,
                       "prices": {**base, **special.get(c.pk, {})}} for c in customers],
        "items": [{"id": i.pk, "name": i.name, "unit": i.base_unit.code, "gst": str(i.gst_rate)} for i in items],
    }
    return render(request, "m/new_order.html", {"catalog": catalog})


@role_required(*MOBILE)
@require_POST
def m_api_orders(request):
    """JSON endpoint used by the phone. Safe to retry: client_uuid makes it idempotent."""
    try:
        data = json.loads(request.body)
        customer = _my_customers(request.user).get(pk=int(data["customer_id"]))
        cu = uuid.UUID(str(data["client_uuid"])) if data.get("client_uuid") else None
        lines = [(Item.objects.get(pk=int(l["item_id"])), D(l["qty"])) for l in data["lines"]]
        order, created = svc.place_order(request.user, customer, lines, cu, (data.get("notes") or "")[:200])
    except (KeyError, ValueError, TypeError, Customer.DoesNotExist, Item.DoesNotExist, InvalidOperation, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid order data."}, status=400)
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": "; ".join(e.messages)}, status=400)
    return JsonResponse({"ok": True, "number": order.number, "status": order.status, "duplicate": not created})


@role_required(*MOBILE)
def m_orders(request):
    orders = Order.objects.filter(salesman=request.user).select_related("customer").prefetch_related("lines__item")[:60]
    return render(request, "m/orders.html", {"orders": orders})


@role_required(*MOBILE)
def m_sales(request):
    """Customer-wise sales for this salesman (rejected/cancelled orders excluded)."""
    rows = {}
    qs = Order.objects.filter(salesman=request.user).exclude(status__in=["REJECTED", "CANCELLED"]).select_related("customer").prefetch_related("lines")
    for o in qs:
        r = rows.setdefault(o.customer_id, {"customer": o.customer, "orders": 0, "ordered": ZERO, "invoiced": ZERO})
        r["orders"] += 1
        r["ordered"] += o.taxable_total
        if hasattr(o, "invoice"):
            r["invoiced"] += o.invoice.taxable_value
    rows = sorted(rows.values(), key=lambda r: -r["ordered"])
    return render(request, "m/sales.html", {"rows": rows, "total": sum((r["ordered"] for r in rows), ZERO)})


def m_service_worker(request):
    js = open(settings.BASE_DIR / "static" / "m" / "sw.js", encoding="utf-8").read()
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/m/"
    return resp
