from decimal import Decimal

from django import template

from masters.utils import inr as _inr

register = template.Library()


@register.filter
def inr(value, places=2):
    return _inr(value, int(places))


@register.filter
def rs(value):
    return "₹" + _inr(value, 2)


@register.filter
def qty(value):
    """Trim trailing zeros: 40.000 -> 40, 19.500 -> 19.5"""
    try:
        d = Decimal(str(value))
    except Exception:
        return value
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


@register.filter
def pct(value):
    try:
        return f"{Decimal(str(value)):+.1f}%"
    except Exception:
        return value


@register.filter
def get_item(d, key):
    return d.get(key)


@register.filter
def badge(status):
    return {
        "SUBMITTED": "secondary", "APPROVED": "primary", "INVOICED": "info", "DISPATCHED": "success",
        "REJECTED": "danger", "CANCELLED": "dark", "ISSUED": "success", "POSTED": "success",
    }.get(status, "secondary")
