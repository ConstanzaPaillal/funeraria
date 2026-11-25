"""Descarga en Excel (.xlsx) de las solicitudes con sus datos, responsables y precios."""
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Articulo, Solicitud, Tarifa

VERDE = "A8DCC1"
MONEDA = '"$"#,##0'
FECHA = "dd-mm-yyyy hh:mm"
BORDE = Border(bottom=Side(style="thin", color="D9D2CE"))


def _local(dt):
    """Excel no admite fechas con zona horaria: se entrega la hora local sin zona."""
    return timezone.localtime(dt).replace(tzinfo=None) if dt else None


def _hoja(ws, titulo, columnas, filas, formatos=None, anchos=None):
    ws.title = titulo
    ws.append(columnas)
    for c in ws[1]:
        c.font = Font(bold=True, color="0D3B29")
        c.fill = PatternFill("solid", fgColor=VERDE)
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    for fila in filas:
        ws.append(fila)
    for fila in ws.iter_rows(min_row=2):
        for c in fila:
            c.border = BORDE
            c.alignment = Alignment(vertical="top", wrap_text=True)
            fmt = (formatos or {}).get(c.column_letter)
            if fmt and c.value is not None:
                c.number_format = fmt
    for i, col in enumerate(columnas, 1):
        ws.column_dimensions[get_column_letter(i)].width = (anchos or {}).get(get_column_letter(i), max(14, min(34, len(col) + 4)))
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions


def libro_solicitudes(solicitudes):
    """`solicitudes` ya viene con precios anotados (services.anotar_precios). Devuelve los bytes del .xlsx."""
    wb = Workbook()
    cols = ["Código", "Fallecido/a", "Tipo de servicio", "Ceremonia", "Lugar de la ceremonia", "Sala de velatorio",
            "Inicio velatorio", "Término velatorio", "Etapa", "Estado", "Familiar de contacto", "Teléfono", "Correo",
            "Responsable (recepción)", "Féretro elegido", "Gama", "Precio del servicio", "Precio del féretro",
            "Total referencial", "Facturado", "Abonado", "Saldo", "Etiquetas", "Documentos pendientes", "Ingresada"]
    filas = []
    from .estados import etiqueta
    for s in solicitudes:
        filas.append([
            s.codigo, s.fallecido, s.get_tipo_servicio_display(), _local(s.ceremonia_inicio), s.lugar_ceremonia,
            str(s.sala) if s.sala else "", _local(s.velatorio_inicio), _local(s.velatorio_fin), s.etapa_texto, etiqueta(s.estado),
            s.contacto_nombre, s.contacto_telefono, s.contacto_email,
            (s.responsable.get_full_name() or s.responsable.username) if s.responsable else "",
            s.feretro.nombre if s.feretro else "", s.feretro.get_gama_display() if s.feretro else "",
            s.precio_servicio, s.precio_feretro, s.total_referencial, s.facturado, s.abonado, s.saldo,
            ", ".join(e.nombre for e in s.etiquetas.all()),
            ", ".join(d.get_tipo_display() for d in s.documentos_pendientes), _local(s.creada)])
    fm = {"D": FECHA, "G": FECHA, "H": FECHA, "V": FECHA, "Q": MONEDA, "R": MONEDA, "S": MONEDA, "T": MONEDA}
    _hoja(wb.active, "Fallecidos y servicios", cols, filas, fm, {"B": 30, "D": 18, "E": 30, "G": 18, "H": 18, "U": 34})
    if solicitudes:
        n = len(solicitudes) + 1
        ws = wb.active
        ws.append([])
        ws.append(["Totales", f"{len(solicitudes)} solicitudes"] + [None] * 14 + [f"=SUM(Q2:Q{n})", f"=SUM(R2:R{n})", f"=SUM(S2:S{n})", f"=SUM(T2:T{n})"])
        for c in ws[n + 2]:
            c.font = Font(bold=True)
            if c.column_letter in "QRST":
                c.number_format = MONEDA

    # Hoja de precios
    ws2 = wb.create_sheet()
    filas2 = [["Féretro", a.nombre, a.get_gama_display(), a.precio, a.stock] for a in
              Articulo.objects.filter(categoria="feretros").order_by("precio")]
    filas2 += [["Otro artículo", a.nombre, a.get_categoria_display(), a.precio, a.stock] for a in
               Articulo.objects.exclude(categoria="feretros").filter(precio__gt=0).order_by("categoria", "nombre")]
    filas2 += [["Servicio", t.get_tipo_servicio_display(), "", t.precio, None] for t in Tarifa.objects.order_by("precio")]
    _hoja(ws2, "Precios", ["Tipo", "Detalle", "Gama / categoría", "Precio (IVA incluido)", "Stock"], filas2,
          {"D": MONEDA}, {"A": 16, "B": 36, "C": 20, "D": 22})
    ws2.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
