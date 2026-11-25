"""Datos de presentación compartidos por las vistas internas y la vista de la familia."""
from .models import Solicitud


def etapas_de(solicitud):
    """Línea de etapas: [(nombre, clase, texto)] con clase 'done', 'now' o ''."""
    resultado = []
    for valor, nombre in Solicitud.Etapa.choices:
        if valor < solicitud.etapa or (solicitud.finalizada and valor == Solicitud.Etapa.FINALIZADO):
            clase, texto = "done", "Completada"
        elif valor == solicitud.etapa:
            clase, texto = "now", "Etapa actual"
        else:
            clase, texto = "", "Pendiente"
        resultado.append((nombre, clase, texto))
    return resultado


MENSAJES_FAMILIA = {
    "en_curso": "Todo está en orden. Nuestro equipo continúa con la preparación del servicio.",
    "docs_pendientes": "Para continuar necesitamos que nos entregue los documentos marcados como pendientes.",
    "urgente": "La ceremonia es en menos de 24 horas y aún faltan documentos. Comuníquese con nosotros lo antes posible.",
    "finalizada": "El servicio ha finalizado. Gracias por permitirnos acompañarles en este momento.",
}
