"""
Datos de los gráficos del panel. Cada gráfico es un diccionario simple que dibuja `_grafico.html`
con HTML y CSS (sin librerías): los valores siempre aparecen como texto, así no dependen del color.
Las barras usan el verde de la marca; el verde/ámbar/rojo se reserva para estados (con su texto).
"""
from datetime import timedelta

from django.utils import timezone

from .estados import ESTADOS
from .models import Articulo, Movimiento, Solicitud, Traslado, Venta

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def clp(n):
    return "$" + f"{int(n):,}".replace(",", ".")


def clp_corto(n):
    n = int(n)
    if n >= 1_000_000:
        return "$" + f"{n / 1_000_000:.1f}".replace(".", ",").replace(",0", "") + " M"
    if n >= 1000:
        return f"${round(n / 1000)} mil"
    return f"${n}"


def _pct(valor, maximo):
    if not maximo or valor <= 0:
        return 0
    return max(3, round(100 * valor / maximo))


def _grafico(id, titulo, tipo, filas, nota=""):
    return {"id": id, "titulo": titulo, "tipo": tipo, "nota": nota, "filas": filas, "sin_datos": not any(f["valor"] for f in filas)}


def _filas(items, formato=str, corto=None):
    """items: [(etiqueta, valor, tono|None)]. `corto` da un texto breve para las etiquetas de columnas angostas."""
    maximo = max([v for _, v, _ in items], default=0)
    return [{"etiqueta": e, "valor": v, "texto": formato(v), "corto": (corto or formato)(v), "pct": _pct(v, maximo), "tono": t or "marca"}
            for e, v, t in items]


def _ultimos_dias(n):
    hoy = timezone.localdate()
    return [hoy - timedelta(days=i) for i in range(n - 1, -1, -1)]


def por_estado(solicitudes):
    cuenta = {}
    for s in solicitudes:
        cuenta[s.estado] = cuenta.get(s.estado, 0) + 1
    orden = ("urgente", "docs_pendientes", "en_curso", "finalizada")
    items = [(ESTADOS[k][1], cuenta.get(k, 0), ESTADOS[k][0]) for k in orden]
    return _grafico("estados", "Solicitudes por estado", "h", _filas(items), "Todas las solicitudes registradas.")


def servicios_por_tipo(solicitudes):
    cuenta = {}
    for s in solicitudes:
        cuenta[s.get_tipo_servicio_display()] = cuenta.get(s.get_tipo_servicio_display(), 0) + 1
    items = sorted(((k, v, None) for k, v in cuenta.items()), key=lambda x: -x[1])
    return _grafico("servicios", "Servicios más solicitados", "h", _filas(items))


def ingresos_mensuales(meses=6):
    hoy = timezone.localdate().replace(day=1)
    claves = []
    for i in range(meses - 1, -1, -1):
        m = hoy.month - 1 - i
        claves.append((hoy.year + m // 12, m % 12 + 1))
    totales = dict.fromkeys(claves, 0)
    desde = timezone.make_aware(timezone.datetime(claves[0][0], claves[0][1], 1))
    for v in Venta.objects.filter(anulada=False, creada__gte=desde).only("creada", "total"):
        f = timezone.localtime(v.creada)
        if (f.year, f.month) in totales:
            totales[(f.year, f.month)] += v.total
    items = [(f"{MESES[m - 1]}" if a == hoy.year else f"{MESES[m - 1]} {a % 100}", t, None) for (a, m), t in totales.items()]
    return _grafico("ingresos", "Ventas por mes", "v", _filas(items, clp, clp_corto), "Boletas y facturas vigentes, IVA incluido.")


def etapas_activas(solicitudes):
    cuenta = {e: 0 for e in Solicitud.Etapa if e != Solicitud.Etapa.FINALIZADO}
    for s in solicitudes:
        if s.etapa in cuenta:
            cuenta[Solicitud.Etapa(s.etapa)] += 1
    return _grafico("etapas", "Mis solicitudes por etapa", "h", _filas([(e.label, n, None) for e, n in cuenta.items()]))


def ceremonias_proximas(solicitudes, dias=14):
    dias_lista = [timezone.localdate() + timedelta(days=i) for i in range(dias)]
    cuenta = dict.fromkeys(dias_lista, 0)
    for s in solicitudes:
        d = timezone.localtime(s.ceremonia_inicio).date()
        if d in cuenta:
            cuenta[d] += 1
    items = [(f"{d.day}/{d.month}", n, None) for d, n in cuenta.items()]
    return _grafico("ceremonias", f"Ceremonias en los próximos {dias} días", "v", _filas(items), "Número de ceremonias por día.")


def stock_vs_minimo(limite=10):
    arts = sorted(Articulo.objects.all(), key=lambda a: a.stock / max(a.stock_minimo, 1))[:limite]
    maximo = max([a.stock for a in arts] + [a.stock_minimo for a in arts], default=0)
    tono = {"stock_normal": "ok", "stock_bajo": "warn", "stock_critico": "crit"}
    filas = [{"etiqueta": a.nombre, "valor": a.stock, "pct": _pct(a.stock, maximo), "marca": _pct(a.stock_minimo, maximo),
              "texto": f"{a.stock} · mín. {a.stock_minimo} · {ESTADOS[a.estado][1]}", "tono": tono[a.estado]} for a in arts]
    return _grafico("stock", "Stock frente al mínimo", "h", filas, "La marca vertical indica el stock mínimo. Se ordena del más urgente al más holgado.")


def salidas_semana():
    dias = _ultimos_dias(7)
    cuenta = dict.fromkeys(dias, 0)
    desde = timezone.make_aware(timezone.datetime.combine(dias[0], timezone.datetime.min.time()))
    for m in Movimiento.objects.filter(tipo="salida", creado__gte=desde).only("creado", "cantidad"):
        d = timezone.localtime(m.creado).date()
        if d in cuenta:
            cuenta[d] += m.cantidad
    items = [(f"{d.day}/{d.month}", n, None) for d, n in cuenta.items()]
    return _grafico("salidas", "Salidas de bodega, últimos 7 días", "v", _filas(items), "Unidades entregadas por día.")


def traslados_semana(chofer=None):
    dias = _ultimos_dias(7)
    cuenta = dict.fromkeys(dias, 0)
    desde = timezone.make_aware(timezone.datetime.combine(dias[0], timezone.datetime.min.time()))
    qs = Traslado.objects.filter(estado="completado", inicio__gte=desde)
    if chofer is not None:
        qs = qs.filter(chofer=chofer)
    for t in qs.only("inicio"):
        d = timezone.localtime(t.inicio).date()
        if d in cuenta:
            cuenta[d] += 1
    items = [(f"{d.day}/{d.month}", n, None) for d, n in cuenta.items()]
    return _grafico("traslados", "Traslados completados, últimos 7 días", "v", _filas(items))
