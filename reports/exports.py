"""Turn a report (title + list of tables) into HTML context, CSV, XLSX or PDF."""
import csv
import re
from io import BytesIO, StringIO

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from decimal import Decimal


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _text(v):
    if isinstance(v, Decimal):
        return f"{v:,.2f}" if v == v.quantize(Decimal("0.01")) else f"{v.normalize():f}"
    return "" if v is None else str(v)


def to_csv(report, filename):
    out = StringIO()
    w = csv.writer(out)
    w.writerow([report["title"], report["subtitle"]])
    for t in report["tables"]:
        w.writerow([])
        w.writerow([t["name"]])
        w.writerow(t["headers"])
        for r in t["rows"]:
            w.writerow([_plain(c) for c in r])
        if t.get("totals"):
            w.writerow([_plain(c) for c in t["totals"]])
    resp = HttpResponse(out.getvalue(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    return resp


def to_xlsx(report, filename):
    wb = Workbook()
    wb.remove(wb.active)
    for t in report["tables"]:
        title = re.sub(r"[\\/*?:\[\]]", "-", t["name"])[:31] or "Sheet"
        n = 2
        while title in wb.sheetnames:                 # sheet names must be unique
            title = f"{title[:28]}~{n}"
            n += 1
        ws = wb.create_sheet(title)
        ws.append([report["title"]])
        ws.append([report["subtitle"]])
        ws.append([])
        ws.append(t["headers"])
        for c in ws[4]:
            c.font = Font(bold=True)
        for r in t["rows"]:
            ws.append([_plain(c) for c in r])
        if t.get("totals"):
            ws.append([_plain(c) for c in t["totals"]])
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
        for i, h in enumerate(t["headers"], 1):
            width = max([len(str(h))] + [len(_text(r[i - 1])) for r in t["rows"] if len(r) >= i] + [8])
            ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 40)
    if not wb.sheetnames:
        wb.create_sheet("Empty")
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return resp


def to_pdf(report, filename):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title=report["title"])
    ss = getSampleStyleSheet()
    small = ss["BodyText"].clone("small", fontSize=7, leading=8.5)
    story = [Paragraph(report["title"], ss["Title"]), Paragraph(report["subtitle"], ss["Normal"]), Spacer(1, 6)]
    for t in report["tables"]:
        story.append(Paragraph(t["name"], ss["Heading3"]))
        data = [[Paragraph(f"<b>{h}</b>", small) for h in t["headers"]]]
        for r in t["rows"]:
            data.append([Paragraph(_text(c).replace("&", "&amp;").replace("<", "&lt;"), small) for c in r])
        if t.get("totals"):
            data.append([Paragraph(f"<b>{_text(c)}</b>", small) for c in t["totals"]])
        width = (297 - 20) * mm / max(len(t["headers"]), 1)
        tb = Table(data, colWidths=[width] * len(t["headers"]), repeatRows=1)
        tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [tb, Spacer(1, 8)]
    doc.build(story)
    resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return resp
