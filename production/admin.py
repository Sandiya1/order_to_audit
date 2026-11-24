from django.contrib import admin

from .models import ProductionRun, ProductionRunLine


class LineInline(admin.TabularInline):
    model = ProductionRunLine
    extra = 0
    can_delete = False
    readonly_fields = ("item", "planned_qty", "actual_qty", "unit_cost", "cost")

    def has_add_permission(self, request, obj=None): return False


@admin.register(ProductionRun)
class RunAdmin(admin.ModelAdmin):
    list_display = ("run_no", "product", "run_date", "expected_output", "good_output", "status")
    inlines = [LineInline]

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_change_permission(self, request, obj=None): return False
