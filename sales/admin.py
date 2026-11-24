from django.contrib import admin

from .models import CreditNote, Dispatch, Invoice, Order, Payment


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(Order)
class OrderAdmin(ReadOnly):
    list_display = ("number", "customer", "salesman", "status", "created_at")


@admin.register(Invoice)
class InvoiceAdmin(ReadOnly):
    list_display = ("number", "date", "customer", "total", "status")


admin.site.register(Payment, ReadOnly)
admin.site.register(CreditNote, ReadOnly)
admin.site.register(Dispatch, ReadOnly)
