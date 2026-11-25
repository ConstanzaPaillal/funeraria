"""
Tabla única de estados: cada estado tiene un tono (ok / warn / crit / neutral) y un texto.
Todas las pantallas dibujan la insignia con esta misma tabla, así un estado se ve igual en el
panel, en los listados y en la vista de la familia. El color nunca va solo: siempre lleva ícono y texto.
"""
ESTADOS = {
    # Solicitudes
    "en_curso": ("ok", "En curso"),
    "docs_pendientes": ("warn", "Documentos pendientes"),
    "urgente": ("crit", "Urgente"),
    "finalizada": ("neutral", "Finalizada"),
    # Inventario
    "stock_normal": ("ok", "Stock normal"),
    "stock_bajo": ("warn", "Stock bajo"),
    "stock_critico": ("crit", "Stock crítico"),
    # Traslados
    "programado": ("neutral", "Programado"),
    "en_camino": ("ok", "En camino"),
    "retrasado": ("warn", "Retrasado"),
    "completado": ("neutral", "Completado"),
    # Flota
    "disponible": ("ok", "Disponible"),
    "en_ruta": ("neutral", "En ruta"),
    "mantencion": ("warn", "En mantención"),
    "fuera_servicio": ("crit", "Fuera de servicio"),
    # Usuarios, pedidos y documentos
    "activo": ("ok", "Activo"),
    "inactivo": ("neutral", "Inactivo"),
    "pendiente": ("warn", "Pendiente"),
    "entregado": ("ok", "Entregado"),
    # Ausencias (pendiente ya existe arriba)
    "ausente": ("warn", "Ausente hoy"),
    "aprobada": ("ok", "Aprobada"),
    "rechazada": ("crit", "Rechazada"),
    # Ventas
    "emitida": ("ok", "Emitida"),
    "anulada": ("crit", "Anulada"),
}

ICONOS = {
    "ok": '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.6 8.2l2.3 2.3 4.6-4.8" fill="none" stroke="var(--b-bg)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "warn": '<path d="M8 1.4L15 14H1z" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M8 6v3.6M8 11.7v.1" stroke="var(--b-bg)" stroke-width="1.8" stroke-linecap="round"/>',
    "crit": '<rect x="1.5" y="1.5" width="13" height="13" rx="3" fill="currentColor"/><path d="M5.3 5.3l5.4 5.4M10.7 5.3l-5.4 5.4" stroke="var(--b-bg)" stroke-width="1.8" stroke-linecap="round"/>',
    "info": '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M8 7.2v4M8 4.6v.1" stroke="var(--b-bg)" stroke-width="1.8" stroke-linecap="round"/>',
    "neutral": '<circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="2"/>',
}

ORDEN_SOLICITUD = {"urgente": 0, "docs_pendientes": 1, "en_curso": 2, "finalizada": 3}


def tono(clave):
    return ESTADOS.get(clave, ("neutral", clave))[0]


def etiqueta(clave):
    return ESTADOS.get(clave, ("neutral", clave))[1]
