from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from ..estados import ESTADOS, ICONOS

register = template.Library()


def _icono(tono, size=14):
    return mark_safe(f'<svg viewBox="0 0 16 16" width="{size}" height="{size}" aria-hidden="true" focusable="false">{ICONOS[tono]}</svg>')


@register.simple_tag
def insignia(clave):
    """Insignia de estado: la misma pieza en panel, listados y vista de la familia (color + ícono + texto)."""
    tono, texto = ESTADOS.get(clave, ("neutral", str(clave)))
    return format_html('<span class="badge badge--{}">{}<span>{}</span></span>', tono, _icono(tono), texto)


@register.simple_tag
def icono(tono, size=18):
    return _icono(tono, size)


@register.filter
def tono_mensaje(tags):
    """Nivel de mensaje de Django -> tono de aviso."""
    primero = str(tags).split()[0] if tags else ""
    return {"success": "ok", "warning": "warn", "error": "crit"}.get(primero, "info")


@register.filter
def attr(obj, ruta):
    for parte in ruta.split("."):
        obj = getattr(obj, parte, "")
        if callable(obj):
            obj = obj()
    return "" if obj is None else obj


@register.simple_tag(takes_context=True)
def qs(context, **cambios):
    """Conserva los filtros GET actuales y cambia/añade los indicados (útil para paginar)."""
    q = context["request"].GET.copy()
    for k, v in cambios.items():
        q[k] = v
    return q.urlencode()


@register.simple_tag
def url_de(prefijo, accion, obj):
    return reverse(f"{prefijo}_{accion}", args=[obj.pk])


@register.filter
def clp(valor):
    """1234567 -> $1.234.567"""
    try:
        return "$" + f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"
