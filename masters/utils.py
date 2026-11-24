"""Money, quantity and date helpers. Money is always Decimal, never float."""
from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")
FOUR = Decimal("0.0001")
THREE = Decimal("0.001")


def D(x):
    """Safe Decimal conversion (goes via str so floats don't leak binary noise)."""
    if isinstance(x, Decimal):
        return x
    if x is None or x == "":
        return Decimal("0")
    return Decimal(str(x).replace(",", "").strip())


def money(x):
    return D(x).quantize(TWO, rounding=ROUND_HALF_UP)


def cost4(x):
    return D(x).quantize(FOUR, rounding=ROUND_HALF_UP)


def qty3(x):
    return D(x).quantize(THREE, rounding=ROUND_HALF_UP)


def fy_label(d):
    """Indian financial year label: 1 Apr 2026 .. 31 Mar 2027 -> '2026-27'."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def fy_start(d):
    import datetime
    y = d.year if d.month >= 4 else d.year - 1
    return datetime.date(y, 4, 1)


_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen "
         "Fifteen Sixteen Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_100(n):
    return _ONES[n] if n < 20 else _TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")


def _below_1000(n):
    out = []
    if n >= 100:
        out.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n:
        out.append(_below_100(n))
    return " ".join(out)


def amount_in_words(amount):
    """Indian numbering (lakh/crore). 7875.50 -> 'Rupees Seven Thousand Eight Hundred Seventy Five and Fifty Paise Only'."""
    amount = money(amount)
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    if rupees == 0 and paise == 0:
        return "Rupees Zero Only"
    parts = []
    for div, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(f"{_below_100(rupees // div) if rupees // div < 100 else _below_1000(rupees // div)} {name}")
            rupees %= div
    if rupees:
        parts.append(_below_1000(rupees))
    text = "Rupees " + " ".join(parts) if parts else "Rupees Zero"
    if paise:
        text += f" and {_below_100(paise)} Paise"
    return text + " Only"


def inr(value, places=2):
    """Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    try:
        v = D(value)
    except Exception:
        return value
    neg = v < 0
    s = f"{abs(v):.{places}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = whole + (f".{frac}" if places else "")
    return ("-" if neg else "") + out
