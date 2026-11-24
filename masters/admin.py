from django.contrib import admin

from .models import BOM, BOMLine, Customer, CustomerPrice, Item, Supplier, Unit


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    list_display = ("product", "yield_qty", "tolerance_pct", "is_active")
    inlines = [BOMLineInline]


class CustomerPriceInline(admin.TabularInline):
    model = CustomerPrice
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "customer_type", "state_code", "gstin", "salesman", "active")
    list_filter = ("customer_type", "state_code", "salesman")
    search_fields = ("name", "gstin")
    inlines = [CustomerPriceInline]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "base_unit", "selling_price", "gst_rate", "reorder_level", "active")
    list_filter = ("kind", "active")
    search_fields = ("code", "name")


admin.site.register(Unit)
admin.site.register(Supplier)
