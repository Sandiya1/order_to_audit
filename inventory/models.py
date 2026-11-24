from decimal import Decimal

from django.conf import settings
from django.db import models

from masters.models import Item, Supplier, Unit


class StockMovement(models.Model):
    """The stock ledger. Every change to stock is one row here. Balances are always
    SUM(qty) over the ledger; nothing is ever overwritten. Rows are immutable - a
    mistake is corrected by a reversing movement that points at the same source."""

    class Type(models.TextChoices):
        OPENING = "OPENING", "Opening balance"
        PURCHASE = "PURCHASE", "Purchase receipt"
        PRODUCTION_ISSUE = "PROD_ISSUE", "Issued to production"
        PRODUCTION_OUTPUT = "PROD_OUTPUT", "Production output"
        SALE = "SALE", "Dispatch / sale"
        SALE_RETURN = "SALE_RETURN", "Sales return (credit note)"
        ADJUSTMENT = "ADJUSTMENT", "Stock adjustment"
        COST_ADJUST = "COST_ADJ", "Cost adjustment (overheads)"
        REVERSAL = "REVERSAL", "Reversal"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    movement_type = models.CharField(max_length=12, choices=Type.choices)
    qty = models.DecimalField("Qty (base unit, signed)", max_digits=16, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    value = models.DecimalField("Value (signed)", max_digits=16, decimal_places=4, default=0)
    source_type = models.CharField(max_length=20)
    source_id = models.PositiveIntegerField(default=0)
    source_ref = models.CharField(max_length=40, blank=True)
    batch_no = models.CharField(max_length=30, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    remarks = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["date", "id"]
        indexes = [models.Index(fields=["item", "date"])]

    def __str__(self):
        return f"{self.date} {self.item.code} {self.qty:+f} ({self.movement_type} {self.source_ref})"

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Stock ledger rows are immutable; post a reversing movement instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Stock ledger rows cannot be deleted.")


class Purchase(models.Model):
    """Raw / packaging material purchase bill. Posting creates PURCHASE ledger rows."""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    bill_no = models.CharField(max_length=40, blank=True)
    date = models.DateField()
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def number(self):
        return f"PUR-{self.pk:04d}"

    def __str__(self):
        return self.number

    @property
    def taxable(self):
        return sum((l.taxable for l in self.lines.all()), Decimal("0"))

    @property
    def tax(self):
        return sum((l.tax for l in self.lines.all()), Decimal("0"))

    @property
    def total(self):
        return self.taxable + self.tax


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    rate = models.DecimalField("Rate per entered unit (ex-GST)", max_digits=12, decimal_places=4)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    qty_base = models.DecimalField(max_digits=16, decimal_places=3)
    batch_no = models.CharField(max_length=30, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    @property
    def taxable(self):
        from masters.utils import money
        return money(self.qty * self.rate)

    @property
    def tax(self):
        from masters.utils import money
        return money(self.taxable * self.gst_rate / 100)
