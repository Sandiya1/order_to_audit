from django import forms
from django.forms import formset_factory

from masters.models import Item, Supplier, Unit


class PurchaseHeaderForm(forms.Form):
    supplier = forms.ModelChoiceField(Supplier.objects.all())
    bill_no = forms.CharField(max_length=40, required=False, label="Supplier bill no")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(max_length=200, required=False)


class PurchaseLineForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(kind__in=["RAW", "PACK"], active=True), required=False)
    qty = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, required=False)
    unit = forms.ModelChoiceField(Unit.objects.all(), required=False)
    rate = forms.DecimalField(max_digits=12, decimal_places=4, min_value=0, required=False, label="Rate (ex-GST)")
    gst_rate = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0, required=False, initial=0, label="GST %")
    batch_no = forms.CharField(max_length=30, required=False)
    expiry_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


PurchaseLineFormSet = formset_factory(PurchaseLineForm, extra=4)


class AdjustmentForm(forms.Form):
    item = forms.ModelChoiceField(Item.objects.filter(active=True))
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    delta = forms.DecimalField(max_digits=14, decimal_places=3, label="Change (+ add / - remove), in the item's base unit")
    reason = forms.CharField(max_length=200)
