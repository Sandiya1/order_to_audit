"""GST maths. Pure functions on Decimal so they are easy to test."""
from decimal import ROUND_HALF_UP, Decimal

from masters.utils import D, money

ZERO = Decimal("0")


def line_tax(taxable, gst_rate, igst):
    """Return dict(cgst, sgst, igst) for a line.

    Intra-state: tax is split half CGST / half SGST. The tax is rounded once to paise and
    SGST takes the remainder so cgst + sgst always equals the line tax exactly.
    Inter-state: the whole amount is IGST.
    """
    taxable = money(taxable)
    tax = money(taxable * D(gst_rate) / 100)
    if igst:
        return {"cgst": ZERO, "sgst": ZERO, "igst": tax}
    cgst = (tax / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"cgst": cgst, "sgst": tax - cgst, "igst": ZERO}


def is_interstate(company_state, place_of_supply):
    return str(company_state) != str(place_of_supply)


def invoice_totals(lines):
    """lines: iterable of dicts with taxable/cgst/sgst/igst -> totals dict."""
    t = {k: ZERO for k in ("taxable", "cgst", "sgst", "igst")}
    for ln in lines:
        for k in t:
            t[k] += ln[k]
    t["total"] = t["taxable"] + t["cgst"] + t["sgst"] + t["igst"]
    return t
