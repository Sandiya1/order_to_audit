from django.urls import path

from . import views

urlpatterns = [
    path("stock/", views.stock_list, name="stock_list"),
    path("stock/adjust/", views.stock_adjust, name="stock_adjust"),
    path("stock/<int:item_id>/ledger/", views.stock_ledger, name="stock_ledger"),
    path("purchases/", views.purchase_list, name="purchase_list"),
    path("purchases/new/", views.purchase_new, name="purchase_new"),
]
