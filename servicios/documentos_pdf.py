"""PDF de boletas y facturas (reportlab)."""
from io import BytesIO

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

VERDE = colors.HexColor("#2f8f68")


def clp(n):
    return "$" + f"{int(n):,}".replace(",", ".")


def pdf_venta(venta):
    e = settings.EMPRESA
    base = getSampleStyleSheet()["Normal"]
    n = ParagraphStyle("n", parent=base, fontName="Helvetica", fontSize=10, leading=13)
    peq = ParagraphStyle("p", parent=n, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    titulo = ParagraphStyle("t", parent=n, fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=1, textColor=VERDE)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"{venta.get_tipo_documento_display()} {venta.numero}", author=e["razon_social"])
    es_factura = venta.tipo_documento == "factura"
    caja = Table([[Paragraph(f"R.U.T.: {e['rut']}", titulo)], [Paragraph(f"{venta.get_tipo_documento_display().upper()}", titulo)],
                  [Paragraph(f"N° {venta.numero}", titulo)]], colWidths=[60 * mm])
    caja.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, VERDE), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    emisor = Paragraph(f"<b>{e['razon_social']}</b><br/>Giro: {e['giro']}<br/>{e['direccion']}<br/>Tel. {e['telefono']}", n)
    cabecera = Table([[emisor, caja]], colWidths=[100 * mm, 60 * mm])
    cabecera.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    cli = [f"<b>Cliente:</b> {venta.cliente_nombre}"]
    if venta.cliente_rut:
        cli.append(f"<b>RUT:</b> {venta.cliente_rut}")
    if es_factura:
        cli += [f"<b>Giro:</b> {venta.cliente_giro}", f"<b>Dirección:</b> {venta.cliente_direccion}"]
    cli += [f"<b>Fecha de emisión:</b> {timezone.localtime(venta.creada):%d-%m-%Y %H:%M}",
            f"<b>Servicio:</b> {venta.solicitud.codigo} - en memoria de {venta.solicitud.fallecido}",
            f"<b>Forma de pago:</b> {venta.get_forma_pago_display()}"]
    datos = [[Paragraph(l, n)] for l in cli]
    datos = Table(datos, colWidths=[160 * mm])
    datos.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))

    filas = [["Descripción", "Cant.", "Precio unit.", "Total"]]
    for i in venta.items.all():
        filas.append([Paragraph(i.descripcion, n), i.cantidad, clp(i.precio_unitario), clp(i.subtotal)])
    items = Table(filas, colWidths=[90 * mm, 16 * mm, 27 * mm, 27 * mm], repeatRows=1)
    items.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#a8dcc1")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0d3b29")),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 10),
                               ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.lightgrey)]))
    tot = [["Neto", clp(venta.neto)], [f"IVA ({int(settings.IVA * 100)} %)", clp(venta.iva)], ["TOTAL", clp(venta.total)]]
    totales = Table(tot, colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    totales.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                                 ("LINEABOVE", (0, 2), (-1, 2), 0.8, VERDE)]))
    partes = [cabecera, Spacer(1, 8 * mm), datos, Spacer(1, 6 * mm), items, Spacer(1, 4 * mm), totales, Spacer(1, 10 * mm)]
    if venta.anulada:
        partes.insert(0, Paragraph("<b>DOCUMENTO ANULADO</b>", ParagraphStyle("a", parent=titulo, textColor=colors.red)))
    partes.append(Paragraph("Comprobante interno del sistema de gestión de Cristo Rey. No reemplaza al documento tributario "
                            "electrónico (DTE) que debe emitirse ante el SII.", peq))
    doc.build(partes)
    return buf.getvalue()
