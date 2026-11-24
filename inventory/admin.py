from django.contrib import admin

from .models import Purchase, StockMovement


@admin.register(StockMovement)
class MovementAdmin(admin.ModelAdmin):
    list_display = ("date", "item", "movement_type", "qty", "value", "source_ref")
    list_filter = ("movement_type", "item")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


admin.site.register(Purchase)
