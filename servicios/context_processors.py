from .permissions import ADMIN, BODEGA, CHOFER, ETIQUETAS, RECEPCION, rol_principal, roles_de


MODULOS = {"home": "panel", "solicitud": "solicitudes", "sala": "salas", "traslado": "traslados", "vehiculo": "flota",
           "articulo": "inventario", "movimiento": "inventario", "pedido": "pedidos", "usuario": "usuarios", "actividad": "actividad",
           "calendario": "calendario", "precios": "precios", "tarifas": "precios", "venta": "ventas", "catalogo": "catalogo", "etiqueta": "etiquetas", "perfil": "perfil", "pago": "ventas", "abono": "solicitudes"}


def roles(request):
    """Expone flags de rol a todos los templates (para mostrar/ocultar menús)."""
    r = roles_de(request.user)
    principal = rol_principal(request.user)
    nombre_url = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
    return {
        "modulo": MODULOS.get(nombre_url.split("_")[0], ""),
        "es_admin": ADMIN in r,
        "es_recepcion": bool(r & {ADMIN, RECEPCION}),
        "es_bodega": bool(r & {ADMIN, BODEGA}),
        "es_chofer": CHOFER in r,
        "rol_texto": ETIQUETAS.get(principal, ""),
    }
