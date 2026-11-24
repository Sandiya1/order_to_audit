"""GST tax-invoice PDF (reportlab). Built-in fonts have no rupee glyph, so amounts use 'Rs.'."""
from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from masters.states import STATE_NAMES
from masters.utils import amount_in_words, inr
from masters.templatetags.ui import qty as fmt_qty


def build_invoice_pdf(inv):
    co = settings.COMPANY
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=inv.number)
    ss = getSampleStyleSheet()
    n = ParagraphStyle("n", parent=ss["Normal"], fontSize=8.5, leading=11)
    b = ParagraphStyle("b", parent=n, fontName="Helvetica-Bold")
    h = ParagraphStyle("h", parent=ss["Title"], fontSize=15, leading=18, spaceAfter=2)
    right = ParagraphStyle("r", parent=n, alignment=2)

    cancelled = inv.status == inv.Status.CANCELLED
    story = [Paragraph(co["name"], h),
             Paragraph(f"{co['address']}<br/>GSTIN: <b>{co['gstin']}</b> &nbsp; State: {co['state_name']} ({co['state_code']})",
                       ParagraphStyle("c", parent=n, alignment=1)),
             Spacer(1, 4),
             Paragraph("TAX INVOICE" + (" - CANCELLED" if cancelled else ""),
                       ParagraphStyle("t", parent=b, alignment=1, fontSize=11, textColor=colors.red if cancelled else colors.black)),
             Spacer(1, 6)]

    meta = Table([[Paragraph(f"<b>Invoice No:</b> {inv.number}<br/><b>Date:</b> {inv.date:%d-%b-%Y}<br/>"
                             f"<b>Order Ref:</b> {inv.order.number}", n),
                   Paragraph(f"<b>Place of supply:</b> {STATE_NAMES.get(inv.place_of_supply, '')} ({inv.place_of_supply})<br/>"
                             f"<b>Supply type:</b> {'Inter-state (IGST)' if inv.is_igst else 'Intra-state (CGST + SGST)'}", n)],
                  [Paragraph(f"<b>Bill to:</b><br/>{inv.buyer_name}<br/>{(inv.buyer_address or '').replace(chr(10), '<br/>')}"
                             f"<br/>GSTIN: {inv.buyer_gstin or 'Unregistered'}", n), ""]],
                 colWidths=[93 * mm, 93 * mm])
    meta.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("SPAN", (0, 1), (1, 1))]))
    story += [meta, Spacer(1, 6)]

    head = ["#", "Description", "HSN", "Qty", "Rate", "Taxable", "GST%"] + (["IGST"] if inv.is_igst else ["CGST", "SGST"]) + ["Amount"]
    data = [head]
    for i, ln in enumerate(inv.lines.all(), 1):
        row = [i, ln.description, ln.hsn_code, fmt_qty(ln.qty), inr(ln.rate), inr(ln.taxable), fmt_qty(ln.gst_rate)]
        row += [inr(ln.igst)] if inv.is_igst else [inr(ln.cgst), inr(ln.sgst)]
        row.append(inr(ln.total))
        data.append(row)
    tot = ["", "Total", "", "", "", inr(inv.taxable_value), ""]
    tot += [inr(inv.igst)] if inv.is_igst else [inr(inv.cgst), inr(inv.sgst)]
    tot.append(inr(inv.total))
    data.append(tot)
    widths = [8, 50, 18, 14, 17, 22, 12] + ([22] if inv.is_igst else [20, 20]) + [24]
    scale = 186 / sum(widths)
    t = Table(data, colWidths=[w * scale * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]))
    story += [t, Spacer(1, 6)]

    summary = Table([["Taxable value", f"Rs. {inr(inv.taxable_value)}"]] +
                    ([["IGST", f"Rs. {inr(inv.igst)}"]] if inv.is_igst else
                     [["CGST", f"Rs. {inr(inv.cgst)}"], ["SGST", f"Rs. {inr(inv.sgst)}"]]) +
                    [["Invoice total", f"Rs. {inr(inv.total)}"]], colWidths=[40 * mm, 35 * mm], hAlign="RIGHT")
    summary.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                 ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]))
    story += [summary, Spacer(1, 6),
              Paragraph(f"<b>Amount in words:</b> {amount_in_words(inv.total)}", n), Spacer(1, 14),
              Paragraph("Declaration: We declare that this invoice shows the actual price of the goods described and that all "
                        "particulars are true and correct. This is a computer-generated invoice.", n), Spacer(1, 18),
              Paragraph(f"For <b>{co['name']}</b><br/><br/><br/>Authorised signatory", right)]
    doc.build(story)
    return buf.getvalue()
