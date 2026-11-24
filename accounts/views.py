from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.shortcuts import redirect

from .models import User


DEMO_LOGINS = [
    ("admin", "Factory admin", "Approves orders, raises GST invoices, records dispatch and payments"),
    ("salesA", "Salesman", "Takes orders on the phone (mobile screens, works offline)"),
    ("prod", "Production supervisor", "Purchases, production runs, planned-vs-actual, stock"),
    ("accountant", "Accountant / auditor", "Read-only reports: sales, GST, stock, outstanding"),
    ("owner", "Owner", "Dashboard: orders, stock, variance alerts, cost per packet, dues"),
]


class LoginView(DjangoLoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.conf import settings
        if getattr(settings, "DEMO_LOGINS", False):
            ctx["demo_logins"] = DEMO_LOGINS
            ctx["demo_password"] = "demo1234"
        return ctx


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def home(request):
    """Send each role to its own landing page."""
    role = request.user.role
    if request.user.is_superuser:
        role = User.Role.ADMIN
    return redirect({
        User.Role.SALESMAN: "m_home",
        User.Role.ADMIN: "dashboard",
        User.Role.OWNER: "dashboard",
        User.Role.PRODUCTION: "production_list",
        User.Role.ACCOUNTANT: "reports_index",
    }[role])
