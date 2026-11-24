from django.contrib import admin
from django.urls import include, path

from accounts import views as account_views

admin.site.site_header = "Order-to-Audit - master data"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", account_views.LoginView.as_view(), name="login"),
    path("logout/", account_views.logout_view, name="logout"),
    path("", account_views.home, name="home"),
    path("", include("sales.urls")),
    path("", include("production.urls")),
    path("", include("inventory.urls")),
    path("reports/", include("reports.urls")),
]
