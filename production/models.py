from decimal import Decimal

from django.conf import settings
from django.db import models

from masters.models import BOM, Item
from masters.utils import D, cost4, money

ZERO = Decimal("0")


def variance_pct(planned, actual):
    planned, actual = D(planned), D(actual)
    if planned == 0:
        return ZERO if actual == 0 else Decimal("100")
    return ((actual - planned) / planned * 100).quantize(Decimal("0.01"))


class ProductionRun(models.Model):
    class Status(models.TextChoices):
        POSTED = "POSTED", "Posted"
        CANCELLED = "CANCELLED", "Cancelled (reversed)"

    run_no = models.CharField(max_length=20, unique=True)
    product = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="runs")
    bom = models.ForeignKey(BOM, on_delete=models.PROTECT)
    run_date = models.DateField()
    batches = models.DecimalField(max_digits=8, decimal_places=3, default=1)
    expected_output = models.DecimalField(max_digits=14, decimal_places=3)
    good_output = models.DecimalField(max_digits=14, decimal_places=3)
    wastage_packets = models.DecimalField("Damaged pouches / scrap packets", max_digits=14, decimal_places=3, default=0)
    tolerance_pct = models.DecimalField(max_digits=5, decimal_places=2)  # snapshot of BOM tolerance
    batch_no = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    notes = models.CharField(max_length=200, blank=True)

    labour_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    power_gas_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_overhead = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overhead_capitalised = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # already in FG value
    material_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-run_date", "-id"]

    def __str__(self):
        return self.run_no

    @property
    def number(self):
        return self.run_no

    @property
    def total_overhead(self):
        return D(self.labour_cost) + D(self.power_gas_cost) + D(self.other_overhead)

    @property
    def total_cost(self):
        return D(self.material_cost) + self.total_overhead

    @property
    def cost_per_packet(self):
        return cost4(self.total_cost / self.good_output) if self.good_output else ZERO

    @property
    def material_cost_per_packet(self):
        return cost4(D(self.material_cost) / self.good_output) if self.good_output else ZERO

    @property
    def margin_per_packet(self):
        price = self.product.selling_price or ZERO
        return price - self.cost_per_packet

    @property
    def margin_pct(self):
        price = self.product.selling_price or ZERO
        return (self.margin_per_packet / price * 100).quantize(Decimal("0.01")) if price else ZERO

    @property
    def yield_variance_pct(self):
        return variance_pct(self.expected_output, self.good_output)

    @property
    def yield_flagged(self):
        return abs(self.yield_variance_pct) > self.tolerance_pct

    @property
    def flagged_lines(self):
        return [l for l in self.lines.all() if l.flagged]

    @property
    def has_alert(self):
        return self.yield_flagged or bool(self.flagged_lines)


class ProductionRunLine(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    planned_qty = models.DecimalField(max_digits=14, decimal_places=3)   # BOM qty x batches, base unit
    actual_qty = models.DecimalField(max_digits=14, decimal_places=3)    # issued, base unit
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)     # WAC at the time of issue
    cost = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        ordering = ["id"]

    @property
    def variance_qty(self):
        return self.actual_qty - self.planned_qty

    @property
    def variance_pct(self):
        return variance_pct(self.planned_qty, self.actual_qty)

    @property
    def flagged(self):
        return abs(self.variance_pct) > self.run.tolerance_pct
