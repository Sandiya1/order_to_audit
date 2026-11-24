from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .states import GST_STATES, STATE_NAMES
from .utils import D


class Unit(models.Model):
    """A unit of measure. `factor` converts 1 unit into the dimension's base unit
    (kg for mass, litre for volume, piece for count)."""
    class Dimension(models.TextChoices):
        MASS = "MASS", "Mass (base kg)"
        VOLUME = "VOLUME", "Volume (base litre)"
        COUNT = "COUNT", "Count (base piece)"

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    dimension = models.CharField(max_length=8, choices=Dimension.choices)
    factor = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    is_carton = models.BooleanField(
        default=False, help_text="Carton of packets: the factor comes from the item's 'units per carton'.")

    class Meta:
        ordering = ["dimension", "factor"]

    def __str__(self):
        return self.code


class Item(models.Model):
    """Raw material, packaging material or finished good. All stock is held in `base_unit`."""
    class Kind(models.TextChoices):
        RAW = "RAW", "Raw material"
        PACK = "PACK", "Packaging"
        FG = "FG", "Finished good"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=4, choices=Kind.choices)
    base_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+")
    hsn_code = models.CharField("HSN", max_length=10, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5"))
    selling_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Price list rate per base unit, before tax (finished goods).")
    units_per_carton = models.PositiveIntegerField(null=True, blank=True, help_text="Packets per carton (finished goods).")
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name

    @property
    def is_fg(self):
        return self.kind == self.Kind.FG

    def clean(self):
        if self.is_fg and not self.hsn_code:
            raise ValidationError({"hsn_code": "Finished goods need an HSN code for GST invoices."})


def factor_for(unit, item):
    """How many base units of `item` are in one `unit`."""
    if unit.is_carton:
        if not item.units_per_carton:
            raise ValidationError(f"{item.name} has no 'units per carton' set; cannot convert cartons.")
        return D(item.units_per_carton)
    return D(unit.factor)


def to_base(qty, unit, item):
    """Convert `qty` of `unit` into the item's base unit, validating the dimension."""
    if not unit.is_carton and unit.dimension != item.base_unit.dimension:
        raise ValidationError(
            f"Cannot convert {unit.code} ({unit.dimension}) to {item.base_unit.code} for {item.name}.")
    base_factor = D(item.base_unit.factor)
    return D(qty) * factor_for(unit, item) / base_factor


class BOM(models.Model):
    """Recipe for one batch of a finished product."""
    product = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="boms",
                                limit_choices_to={"kind": "FG"})
    name = models.CharField(max_length=80, blank=True)
    yield_qty = models.DecimalField("Expected packets per batch", max_digits=12, decimal_places=3)
    tolerance_pct = models.DecimalField("Variance tolerance %", max_digits=5, decimal_places=2, default=Decimal("3"))
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Bill of Materials"
        verbose_name_plural = "Bills of Materials"

    def __str__(self):
        return f"BOM {self.product.name} ({self.yield_qty:f} per batch)"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:  # one active recipe per product
            BOM.objects.filter(product=self.product, is_active=True).exclude(pk=self.pk).update(is_active=False)


class BOMLine(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, limit_choices_to={"kind__in": ["RAW", "PACK"]})
    qty = models.DecimalField("Qty per batch", max_digits=14, decimal_places=3)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    is_primary_pack = models.BooleanField(
        default=False,
        help_text="The pouch/primary pack: consumption defaults to good output + damaged pieces.")

    def __str__(self):
        return f"{self.item.name}: {self.qty:f} {self.unit.code}"

    def clean(self):
        if self.item_id and self.unit_id:
            to_base(1, self.unit, self.item)

    @property
    def qty_base(self):
        return to_base(self.qty, self.unit, self.item)


class Customer(models.Model):
    class Type(models.TextChoices):
        RETAILER = "RETAILER", "Retailer"
        DISTRIBUTOR = "DISTRIBUTOR", "Distributor"

    name = models.CharField(max_length=120)
    customer_type = models.CharField(max_length=12, choices=Type.choices, default=Type.RETAILER)
    gstin = models.CharField("GSTIN", max_length=15, blank=True)
    state_code = models.CharField(max_length=2, choices=GST_STATES, default="33")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=15, blank=True, help_text="With country code for WhatsApp, e.g. 919876543210")
    salesman = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="customers", limit_choices_to={"role": "SALESMAN"})
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def state_name(self):
        return STATE_NAMES.get(self.state_code, self.state_code)


class CustomerPrice(models.Model):
    """Optional customer-specific price (scheme) overriding the price list."""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="prices")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, limit_choices_to={"kind": "FG"})
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = ("customer", "item")


def price_for(customer, item):
    cp = CustomerPrice.objects.filter(customer=customer, item=item).first()
    return cp.price if cp else (item.selling_price or Decimal("0"))


class Supplier(models.Model):
    name = models.CharField(max_length=120)
    gstin = models.CharField("GSTIN", max_length=15, blank=True)
    state_code = models.CharField(max_length=2, choices=GST_STATES, default="33")
    phone = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
