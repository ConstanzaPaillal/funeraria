"""
Conexión con Transbank Webpay Plus (SDK oficial). Es la única parte que habla con Transbank, para poder
reemplazarla en las pruebas. En ambiente «integration» usa las credenciales públicas de pruebas.
"""
from django.conf import settings


def _transaccion():
    from transbank.common.integration_type import IntegrationType
    from transbank.common.options import WebpayOptions
    from transbank.webpay.webpay_plus.transaction import Transaction
    tipo = IntegrationType.LIVE if settings.TRANSBANK_ENV == "production" else IntegrationType.TEST
    return Transaction(WebpayOptions(settings.TRANSBANK_COMMERCE_CODE, settings.TRANSBANK_API_KEY, tipo))


def crear(orden_compra, sesion, monto, url_retorno):
    """Devuelve (url_de_pago, token)."""
    r = _transaccion().create(orden_compra, sesion, monto, url_retorno)
    return r["url"], r["token"]


def confirmar(token):
    """Devuelve (aprobado: bool, datos: dict con authorization_code y card_detail.card_number)."""
    r = _transaccion().commit(token)
    aprobado = r.get("status") == "AUTHORIZED" and r.get("response_code") == 0
    return aprobado, r
