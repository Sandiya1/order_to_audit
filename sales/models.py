import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from masters.models import Customer, Item
from masters.utils import D, money

ZERO = Decimal("0")


class Order(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        INVOICED = "INVOICED", "Invoiced"
        DISPATCHED = "DISPATCHED", "Dispatched"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    number = models.CharField(max_length=20, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    salesman = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    notes = models.CharField(max_length=200, blank=True)
    client_uuid = models.UUIDField(null=True, blank=True, unique=True,
                                   help_text="Generated on the phone; makes offline re-sync idempotent.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reject_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.number

    @property
    def taxable_total(self):
        return sum((l.amount for l in self.lines.all()), ZERO)


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    rate = models.DecimalField(max_digits=12, decimal_places=2)

    @property
    def amount(self):
        return money(self.qty * self.rate)


class Invoice(models.Model):
    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        CANCELLED = "CANCELLED", "Cancelled (credit note)"

    number = models.CharField(max_length=24, unique=True)       # INV/2026-27/0001
    fy = models.CharField(max_length=7)
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="invoice")
    date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ISSUED)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    # Snapshot of the buyer at the time of invoicing (later master edits must not alter a tax invoice)
    buyer_name = models.CharField(max_length=120)
    buyer_gstin = models.CharField(max_length=15, blank=True)
    buyer_address = models.TextField(blank=True)
    place_of_supply = models.CharField(max_length=2)
    is_igst = models.BooleanField(default=False)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    share_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.number

    @property
    def paid(self):
        return self.payments.aggregate(s=models.Sum("amount"))["s"] or ZERO

    @property
    def balance(self):
        return ZERO if self.status == self.Status.CANCELLED else self.total - self.paid

    @property
    def is_dispatched(self):
        return hasattr(self, "dispatch")


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    description = models.CharField(max_length=120)
    hsn_code = models.CharField(max_length=10)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    taxable = models.DecimalField(max_digits=14, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    @property
    def total(self):
        return self.taxable + self.cgst + self.sgst + self.igst


class Dispatch(models.Model):
    number = models.CharField(max_length=20, unique=True)
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="dispatch")
    date = models.DateField()
    vehicle_no = models.CharField(max_length=20, blank=True)
    remarks = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class CreditNote(models.Model):
    """Posted invoices are never deleted: cancelling one issues a credit note."""
    number = models.CharField(max_length=24, unique=True)       # CN/2026-27/0001
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="credit_note")
    date = models.DateField()
    reason = models.CharField(max_length=200)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class Payment(models.Model):
    class Mode(models.TextChoices):
        CASH = "CASH", "Cash"
        UPI = "UPI", "UPI"
        BANK = "BANK", "Bank transfer"
        CHEQUE = "CHEQUE", "Cheque"

    number = models.CharField(max_length=20, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    mode = models.CharField(max_length=7, choices=Mode.choices, default=Mode.UPI)
    reference = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
