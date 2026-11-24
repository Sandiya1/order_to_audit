from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("i/<uuid:token>.pdf", views.invoice_public_pdf, name="invoice_public_pdf"),
    path("payments/new/", views.payment_new, name="payment_new"),
    path("outstanding/", views.outstanding, name="outstanding"),
    path("audit/", views.audit_log, name="audit_log"),
    # mobile
    path("m/", views.m_home, name="m_home"),
    path("m/new/", views.m_new_order, name="m_new"),
    path("m/orders/", views.m_orders, name="m_orders"),
    path("m/sales/", views.m_sales, name="m_sales"),
    path("m/api/orders/", views.m_api_orders, name="m_api_orders"),
    path("m/sw.js", views.m_service_worker, name="m_sw"),
]
