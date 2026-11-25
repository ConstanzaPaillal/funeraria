"""
Lógica de negocio de Cristo Rey.

Está fuera de las vistas para reutilizarla desde templates, API REST, admin y comandos.
Todo lo que modifica stock o reserva recursos corre dentro de transacciones con bloqueo de filas.
"""
import hmac
import re
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import webpay
from .models import (Abono, Actividad, Ausencia, Articulo, Documento, Movimiento, Pedido, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta,
                     VentaItem)


# --------------------------------------------------------------------------
# Registro de actividad
# --------------------------------------------------------------------------
def registrar(usuario, accion, objeto=""):
    Actividad.objects.create(usuario=usuario if getattr(usuario, "pk", None) else None, accion=accion[:200], objeto=str(objeto)[:120])


# --------------------------------------------------------------------------
# Salas y traslados: sin solapamientos
# --------------------------------------------------------------------------
def validar_sala(solicitud):
    """La sala no puede tener otro velatorio en el mismo horario."""
    choque = Solicitud.objects.filter(
        sala_id=solicitud.sala_id, velatorio_inicio__lt=solicitud.velatorio_fin, velatorio_fin__gt=solicitud.velatorio_inicio,
    ).exclude(etapa=Solicitud.Etapa.FINALIZADO).exclude(pk=solicitud.pk)
    otra = choque.first()
    if otra:
        raise ValidationError(f"La sala ya está reservada en ese horario por la solicitud {otra.codigo}.")


def validar_traslado(traslado):
    """Vehículo utilizable, chofer válido y ningún choque de horario de vehículo ni de chofer."""
    errores = []
    if not traslado.vehiculo.utilizable:
        errores.append(f"El vehículo «{traslado.vehiculo.nombre}» está {traslado.vehiculo.get_estado_display().lower()}.")
    chofer = traslado.chofer
    if not chofer.is_active or not chofer.groups.filter(name="Chofer").exists():
        errores.append("La persona asignada no es un chofer activo.")
    fin = traslado.calcular_fin()
    d_ini, d_fin = timezone.localtime(traslado.inicio).date(), timezone.localtime(fin).date()
    ausencia = Ausencia.objects.filter(usuario_id=traslado.chofer_id, estado=Ausencia.Estado.APROBADA,
                                       desde__lte=d_fin, hasta__gte=d_ini).first()
    if ausencia:
        errores.append(f"El chofer tiene una ausencia aprobada ({ausencia.get_tipo_display().lower()}, del {ausencia.desde:%d/%m} al {ausencia.hasta:%d/%m}).")
    activos = Traslado.objects.filter(estado__in=Traslado.ACTIVOS, inicio__lt=fin, fin__gt=traslado.inicio).exclude(pk=traslado.pk)
    if activos.filter(vehiculo_id=traslado.vehiculo_id).exists():
        errores.append("El vehículo ya tiene un traslado en ese horario.")
    if activos.filter(chofer_id=traslado.chofer_id).exists():
        errores.append("El chofer ya tiene un traslado en ese horario.")
    if errores:
        raise ValidationError(errores)


TRANSICIONES = {
    "programado": {"en_camino"},
    "en_camino": {"retrasado", "completado"},
    "retrasado": {"completado"},
    "completado": set(),
}


@transaction.atomic
def cambiar_estado_traslado(traslado, nuevo, usuario):
    traslado = Traslado.objects.select_for_update().select_related("vehiculo").get(pk=traslado.pk)
    if nuevo not in TRANSICIONES.get(traslado.estado, set()):
        raise ValidationError(f"No se puede pasar de «{traslado.get_estado_display()}» a «{Traslado.Estado(nuevo).label}».")
    traslado.estado = nuevo
    traslado.save()
    veh = traslado.vehiculo
    if nuevo == Traslado.Estado.EN_CAMINO and veh.estado == Vehiculo.Estado.DISPONIBLE:
        veh.estado = Vehiculo.Estado.EN_RUTA
        veh.save(update_fields=["estado"])
    if nuevo == Traslado.Estado.COMPLETADO and veh.estado == Vehiculo.Estado.EN_RUTA:
        sigue = Traslado.objects.filter(vehiculo=veh, estado__in=[Traslado.Estado.EN_CAMINO, Traslado.Estado.RETRASADO]).exclude(pk=traslado.pk).exists()
        if not sigue:
            veh.estado = Vehiculo.Estado.DISPONIBLE
            veh.save(update_fields=["estado"])
    registrar(usuario, f"Traslado {traslado.codigo}: {Traslado.Estado(nuevo).label}", traslado.solicitud.codigo)
    return traslado


# --------------------------------------------------------------------------
# Solicitudes: documentos y etapas
# --------------------------------------------------------------------------
@transaction.atomic
def marcar_documento(documento, entregado, usuario):
    documento.entregado = bool(entregado)
    documento.entregado_en = timezone.now() if entregado else None
    documento.save(update_fields=["entregado", "entregado_en"])
    registrar(usuario, f"Documento «{documento.get_tipo_display()}»: {'entregado' if entregado else 'pendiente'}", documento.solicitud.codigo)
    return documento


@transaction.atomic
def avanzar_etapa(solicitud, usuario):
    solicitud = Solicitud.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.finalizada:
        raise ValidationError("El servicio ya está finalizado.")
    siguiente = solicitud.etapa + 1
    if siguiente == Solicitud.Etapa.CEREMONIA and solicitud.documentos.filter(entregado=False).exists():
        raise ValidationError("Faltan documentos: complétalos antes de avanzar a la ceremonia.")
    solicitud.etapa = siguiente
    solicitud.save()
    registrar(usuario, f"Etapa: {solicitud.etapa_texto}", solicitud.codigo)
    return solicitud


# --------------------------------------------------------------------------
# Bodega: stock atómico
# --------------------------------------------------------------------------
@transaction.atomic
def registrar_movimiento(articulo_id, tipo, cantidad, usuario, motivo="", pedido=None):
    if cantidad < 1:
        raise ValidationError("La cantidad debe ser de 1 o más.")
    articulo = Articulo.objects.select_for_update().get(pk=articulo_id)
    if tipo == Movimiento.Tipo.SALIDA:
        if cantidad > articulo.stock:
            raise ValidationError(f"No puedes sacar {cantidad}: en bodega solo hay {articulo.stock} de «{articulo.nombre}».")
        articulo.stock -= cantidad
    else:
        articulo.stock += cantidad
    articulo.save(update_fields=["stock"])
    mov = Movimiento.objects.create(articulo=articulo, tipo=tipo, cantidad=cantidad, motivo=motivo[:150], pedido=pedido, usuario=usuario if getattr(usuario, "pk", None) else None)
    registrar(usuario, f"{mov.get_tipo_display()}: {cantidad} × {articulo.nombre}", articulo.nombre)
    return mov


@transaction.atomic
def entregar_pedido(pedido, usuario):
    """Descuenta del stock todo el pedido o nada: si un artículo no alcanza, no se toca ninguno."""
    pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
    if pedido.estado == Pedido.Estado.ENTREGADO:
        raise ValidationError("Este pedido ya fue entregado.")
    items = list(pedido.items.select_related("articulo").order_by("articulo_id"))
    if not items:
        raise ValidationError("El pedido no tiene materiales.")
    bloqueados = {a.pk: a for a in Articulo.objects.select_for_update().filter(pk__in=[i.articulo_id for i in items]).order_by("pk")}
    faltantes = [f"{i.articulo.nombre} (pedido {i.cantidad}, hay {bloqueados[i.articulo_id].stock})" for i in items if bloqueados[i.articulo_id].stock < i.cantidad]
    if faltantes:
        raise ValidationError("No hay stock suficiente: " + "; ".join(faltantes) + ". Registra una entrada antes de entregar.")
    for i in items:
        registrar_movimiento(i.articulo_id, Movimiento.Tipo.SALIDA, i.cantidad, usuario, motivo=f"Pedido {pedido.codigo} · {pedido.solicitud.codigo}", pedido=pedido)
    pedido.estado = Pedido.Estado.ENTREGADO
    pedido.entregado_en = timezone.now()
    pedido.entregado_por = usuario if getattr(usuario, "pk", None) else None
    pedido.save()
    registrar(usuario, f"Pedido {pedido.codigo} entregado", pedido.solicitud.codigo)
    return pedido


# --------------------------------------------------------------------------
# Vista pública de la familia
# --------------------------------------------------------------------------
def _ip(request):
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def intentos_agotados(request):
    return cache.get(f"familia:{_ip(request)}", 0) >= settings.FAMILIA_MAX_INTENTOS


def sumar_intento(request):
    clave = f"familia:{_ip(request)}"
    cache.add(clave, 0, settings.FAMILIA_VENTANA_SEG)
    try:
        cache.incr(clave)
    except ValueError:
        cache.set(clave, 1, settings.FAMILIA_VENTANA_SEG)


def verificar_familia(codigo, verificacion):
    """Devuelve la solicitud si el código y los últimos 4 dígitos del teléfono coinciden; si no, None."""
    codigo = (codigo or "").strip().upper()
    digitos = "".join(re.findall(r"\d", verificacion or ""))
    solicitud = Solicitud.objects.filter(codigo=codigo).prefetch_related("documentos").select_related("responsable", "sala").first()
    esperado = solicitud.verificacion_familia if solicitud else "0000"
    ok = hmac.compare_digest(digitos.encode(), esperado.encode())
    return solicitud if (solicitud and ok) else None


# --------------------------------------------------------------------------
# Avisos por correo
# --------------------------------------------------------------------------
def solicitudes_para_avisar(dias=None):
    dias = settings.DIAS_AVISO_DOCUMENTOS if dias is None else dias
    ahora = timezone.now()
    qs = (Solicitud.objects.exclude(etapa=Solicitud.Etapa.FINALIZADO).exclude(contacto_email="")
          .filter(ceremonia_inicio__gte=ahora, ceremonia_inicio__lte=ahora + timedelta(days=dias),
                  documentos__entregado=False).distinct().prefetch_related("documentos"))
    # Un aviso por solicitud cada 24 horas como máximo
    return [s for s in qs if not s.aviso_enviado_en or s.aviso_enviado_en < ahora - timedelta(hours=24)]


def avisar_documentos(dias=None, simular=False):
    enviados, fallidos = 0, 0
    for s in solicitudes_para_avisar(dias):
        if simular:
            enviados += 1
            continue
        faltan = "\n".join(f" - {d.get_tipo_display()}" for d in s.documentos_pendientes)
        cuerpo = (f"Estimado/a {s.contacto_nombre},\n\nPara realizar la ceremonia de {s.fallecido} el "
                  f"{timezone.localtime(s.ceremonia_inicio):%d/%m/%Y a las %H:%M} necesitamos los siguientes documentos:\n{faltan}\n\n"
                  f"Puede entregarlos en recepción o llamarnos. Puede ver el avance del servicio con el código {s.codigo}.\n\nFuneraria Cristo Rey")
        try:
            send_mail(f"Documentos pendientes · servicio {s.codigo}", cuerpo, settings.DEFAULT_FROM_EMAIL, [s.contacto_email])
        except Exception:  # un correo fallido no debe detener a los demás
            fallidos += 1
            continue
        Solicitud.objects.filter(pk=s.pk).update(aviso_enviado_en=timezone.now())
        registrar(None, "Aviso de documentos pendientes enviado a la familia", s.codigo)
        enviados += 1
    return enviados, fallidos


# --------------------------------------------------------------------------
# Búsqueda de solicitudes (la usan el listado y la descarga en Excel)
# --------------------------------------------------------------------------
FILTROS = ("q", "estado", "servicio", "responsable", "etiqueta", "desde", "hasta")


def _fecha(valor):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def buscar_solicitudes(params):
    """Aplica los filtros del buscador. `params` es request.GET (o un dict). Devuelve una lista ordenada."""
    from .estados import ORDEN_SOLICITUD
    q = (params.get("q") or "").strip()
    qs = Solicitud.objects.select_related("responsable", "sala", "feretro").prefetch_related("documentos", "ventas", "abonos", "etiquetas")
    if q:
        digitos = "".join(re.findall(r"\d", q))
        filtro = Q(codigo__icontains=q) | Q(fallecido__icontains=q) | Q(contacto_nombre__icontains=q)
        if len(digitos) >= 4:
            filtro |= Q(contacto_telefono__icontains=digitos[-4:])
        qs = qs.filter(filtro)
    if params.get("servicio") in dict(Solicitud.Servicio.choices):
        qs = qs.filter(tipo_servicio=params["servicio"])
    if str(params.get("etiqueta") or "").isdigit():
        qs = qs.filter(etiquetas__id=int(params["etiqueta"]))
    if str(params.get("responsable") or "").isdigit():
        qs = qs.filter(responsable_id=int(params["responsable"]))
    if (d := _fecha(params.get("desde"))):
        qs = qs.filter(ceremonia_inicio__date__gte=d)
    if (h := _fecha(params.get("hasta"))):
        qs = qs.filter(ceremonia_inicio__date__lte=h)
    lista = list(qs)
    if params.get("estado"):
        lista = [s for s in lista if s.estado == params["estado"]]
    lista.sort(key=lambda s: (ORDEN_SOLICITUD[s.estado], s.ceremonia_inicio))
    anotar_precios(lista)
    return lista


def tarifas():
    return {t.tipo_servicio: t.precio for t in Tarifa.objects.all()}


def anotar_precios(solicitudes):
    """Agrega a cada solicitud el precio del servicio, del féretro elegido, el total de referencia y lo facturado."""
    tf = tarifas()
    for s in solicitudes:
        s.precio_servicio = tf.get(s.tipo_servicio, 0)
        s.precio_feretro = s.feretro.precio if s.feretro_id else 0
        s.total_referencial = s.precio_servicio + s.precio_feretro
        s.facturado = sum(v.total for v in s.ventas.all() if not v.anulada)
        s.abonado = sum(a.monto for a in s.abonos.all() if a.cuenta)
        s.saldo = max(s.total_referencial - s.abonado, 0)
    return solicitudes


# --------------------------------------------------------------------------
# Ventas: boleta / factura
# --------------------------------------------------------------------------
def desglose_iva(total):
    """Los precios incluyen IVA: neto = total / (1 + IVA)."""
    neto = round(total / (1 + settings.IVA))
    return neto, total - neto


@transaction.atomic
def crear_venta(solicitud, usuario, datos, lineas):
    """
    `datos`: campos del cliente y forma de pago. `lineas`: [(descripcion, cantidad, precio_unitario, articulo|None)].
    Calcula neto, IVA y total, y registra la actividad. No toca el stock: la entrega de materiales va por pedidos de bodega.
    """
    if not lineas:
        raise ValidationError("Agrega al menos un producto o servicio a la venta.")
    if any(c < 1 or p < 0 for _, c, p, _ in lineas):
        raise ValidationError("Las cantidades deben ser de 1 o más y los precios no pueden ser negativos.")
    total = sum(c * p for _, c, p, _ in lineas)
    if total <= 0:
        raise ValidationError("El total de la venta debe ser mayor que cero.")
    neto, iva = desglose_iva(total)
    venta = Venta(solicitud=solicitud, creada_por=usuario if getattr(usuario, "pk", None) else None, neto=neto, iva=iva, total=total, **datos)
    venta.full_clean(exclude=["numero"])
    venta.save()
    VentaItem.objects.bulk_create([VentaItem(venta=venta, descripcion=d[:150], cantidad=c, precio_unitario=p, articulo=a) for d, c, p, a in lineas])
    registrar(usuario, f"{venta.get_tipo_documento_display()} {venta.numero} emitida (${total:,})".replace(",", "."), solicitud.codigo)
    return venta


@transaction.atomic
def anular_venta(venta, usuario):
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    if venta.anulada:
        raise ValidationError("Este documento ya está anulado.")
    venta.anulada, venta.anulada_en = True, timezone.now()
    venta.save(update_fields=["anulada", "anulada_en"])
    registrar(usuario, f"{venta.get_tipo_documento_display()} {venta.numero} anulada", venta.solicitud.codigo)
    return venta


# --------------------------------------------------------------------------
# Ausencias del personal
# --------------------------------------------------------------------------
def _rango_fechas(desde, hasta):
    ini = timezone.make_aware(timezone.datetime.combine(desde, timezone.datetime.min.time()))
    return ini, ini.replace(hour=0) + timedelta(days=(hasta - desde).days + 1)


@transaction.atomic
def solicitar_ausencia(usuario, datos, por=None):
    """
    Cada persona pide su ausencia (queda pendiente). Si la registra el administrador, queda aprobada.
    El personal no puede pedir fechas ya pasadas; el administrador sí (por ejemplo, para registrar una licencia).
    """
    por = por or usuario
    es_admin = bool(getattr(por, "is_superuser", False))
    aus = Ausencia(usuario=usuario, **datos)
    if not es_admin and aus.desde and aus.desde < timezone.localdate():
        raise ValidationError({"desde": "No puedes pedir una ausencia con fecha pasada. Habla con el administrador."})
    aus.full_clean()
    if es_admin:
        aus.estado, aus.resuelta_por, aus.resuelta_en = Ausencia.Estado.APROBADA, por, timezone.now()
    aus.save()
    registrar(por, f"Ausencia {'registrada' if es_admin else 'solicitada'}: {aus}", usuario.get_full_name() or usuario.username)
    return aus


@transaction.atomic
def resolver_ausencia(ausencia, accion, admin, comentario=""):
    """El administrador aprueba o rechaza. No se aprueba si el chofer tiene traslados en esas fechas."""
    ausencia = Ausencia.objects.select_for_update().select_related("usuario").get(pk=ausencia.pk)
    if ausencia.estado != Ausencia.Estado.PENDIENTE:
        raise ValidationError(f"Esta solicitud ya fue {ausencia.get_estado_display().lower()}.")
    if accion not in ("aprobar", "rechazar"):
        raise ValidationError("Acción no válida.")
    if accion == "aprobar":
        ini, fin = _rango_fechas(ausencia.desde, ausencia.hasta)
        trs = list(Traslado.objects.filter(chofer=ausencia.usuario, estado__in=Traslado.ACTIVOS, inicio__gte=ini, inicio__lt=fin))
        if trs:
            raise ValidationError("No se puede aprobar: esta persona tiene traslados programados en esas fechas ("
                                  + ", ".join(t.codigo for t in trs) + "). Reasígnalos primero.")
    ausencia.estado = Ausencia.Estado.APROBADA if accion == "aprobar" else Ausencia.Estado.RECHAZADA
    ausencia.resuelta_por, ausencia.resuelta_en, ausencia.comentario = admin, timezone.now(), (comentario or "")[:200]
    ausencia.save()
    registrar(admin, f"Ausencia {ausencia.get_estado_display().lower()}: {ausencia}", ausencia.usuario.get_full_name() or ausencia.usuario.username)
    return ausencia


@transaction.atomic
def cancelar_ausencia(ausencia, usuario):
    """Quien la pidió puede retirarla mientras esté pendiente; el administrador puede eliminar cualquiera."""
    ausencia = Ausencia.objects.select_for_update().get(pk=ausencia.pk)
    es_admin = bool(getattr(usuario, "is_superuser", False))
    if not es_admin and (ausencia.usuario_id != usuario.pk or ausencia.estado != Ausencia.Estado.PENDIENTE):
        raise ValidationError("Solo puedes retirar tus solicitudes pendientes.")
    texto = str(ausencia)
    ausencia.delete()
    registrar(usuario, f"Ausencia eliminada: {texto}", "")


def ausentes_en(dia):
    """Ausencias aprobadas que cubren `dia`."""
    return Ausencia.objects.filter(estado=Ausencia.Estado.APROBADA, desde__lte=dia, hasta__gte=dia).select_related("usuario")


# --------------------------------------------------------------------------
# Abonos de reserva y pagos con Webpay
# --------------------------------------------------------------------------
def _saldo(solicitud):
    anotada = anotar_precios([Solicitud.objects.select_related("feretro").prefetch_related("ventas", "abonos").get(pk=solicitud.pk)])[0]
    return anotada.total_referencial, anotada.abonado, anotada.saldo


def _validar_monto(solicitud, monto):
    total, abonado, saldo = _saldo(solicitud)
    if total <= 0:
        raise ValidationError("Esta solicitud aún no tiene precio: elige el féretro o define la tarifa del servicio.")
    if monto < 1000:
        raise ValidationError("El abono mínimo es de $1.000.")
    if monto > saldo:
        raise ValidationError(f"El abono ({_clp(monto)}) supera el saldo pendiente ({_clp(saldo)}).")


def _clp(n):
    return "$" + f"{int(n):,}".replace(",", ".")


@transaction.atomic
def registrar_abono(solicitud, usuario, monto, medio, referencia=""):
    """Abono manual (efectivo, transferencia, tarjeta en el local). El pago online va por iniciar_webpay."""
    if medio == Abono.Medio.WEBPAY or medio not in dict(Abono.Medio.choices):
        raise ValidationError("Para cobrar con Webpay usa el botón «Pagar con Webpay».")
    Solicitud.objects.select_for_update().get(pk=solicitud.pk)
    _validar_monto(solicitud, monto)
    ab = Abono.objects.create(solicitud=solicitud, monto=monto, medio=medio, referencia=referencia.strip(),
                              estado=Abono.Estado.CONFIRMADO, registrado_por=usuario)
    registrar(usuario, f"Abono de {_clp(monto)} ({ab.get_medio_display()})", solicitud.codigo)
    return ab


@transaction.atomic
def anular_abono(abono, usuario):
    abono = Abono.objects.select_for_update().get(pk=abono.pk)
    if abono.estado != Abono.Estado.CONFIRMADO:
        raise ValidationError("Solo se pueden anular abonos confirmados.")
    if abono.medio == Abono.Medio.WEBPAY:
        raise ValidationError("Un pago con Webpay no se anula aquí: la devolución se pide a Transbank.")
    abono.estado = Abono.Estado.ANULADO
    abono.save(update_fields=["estado"])
    registrar(usuario, f"Abono anulado ({_clp(abono.monto)})", abono.solicitud.codigo)
    return abono


def iniciar_webpay(solicitud, usuario, monto, url_retorno):
    """Crea el abono pendiente y la transacción en Transbank. Devuelve (url, token)."""
    with transaction.atomic():
        Solicitud.objects.select_for_update().get(pk=solicitud.pk)
        _validar_monto(solicitud, monto)
        ab = Abono.objects.create(solicitud=solicitud, monto=monto, medio=Abono.Medio.WEBPAY,
                                  estado=Abono.Estado.PENDIENTE, registrado_por=usuario)
        ab.orden_compra = f"CR{ab.pk:08d}"
        ab.save(update_fields=["orden_compra"])
    try:
        url, token = webpay.crear(ab.orden_compra, f"sol{solicitud.pk}", monto, url_retorno)
    except Exception:
        ab.estado = Abono.Estado.RECHAZADO
        ab.referencia = "No se pudo iniciar el pago"
        ab.save(update_fields=["estado", "referencia"])
        raise ValidationError("No pudimos conectar con Webpay. Inténtalo de nuevo en unos minutos.")
    ab.token = token
    ab.save(update_fields=["token"])
    registrar(usuario, f"Pago Webpay iniciado ({_clp(monto)})", solicitud.codigo)
    return url, token


def cerrar_webpay(token, cancelado=False):
    """
    Resultado del pago al volver de Webpay. Es idempotente: si el abono ya se resolvió, solo lo devuelve.
    Devuelve el Abono, o None si el token no corresponde a ninguno.
    """
    with transaction.atomic():
        ab = Abono.objects.select_for_update().select_related("solicitud").filter(Q(token=token) | Q(orden_compra=token)).first()
        if ab is None or ab.estado != Abono.Estado.PENDIENTE:
            return ab
        if cancelado:
            ab.estado, ab.referencia = Abono.Estado.RECHAZADO, "Pago cancelado por el usuario"
        else:
            try:
                aprobado, r = webpay.confirmar(token)
            except Exception:
                # Sin respuesta de Transbank no se sabe si se cobró: se deja pendiente para reintentar.
                ab.error_conexion = True
                return ab
            if aprobado and r.get("amount") == ab.monto:
                ab.estado = Abono.Estado.CONFIRMADO
                ab.codigo_autorizacion = str(r.get("authorization_code", ""))[:12]
                ab.tarjeta_ultimos4 = str((r.get("card_detail") or {}).get("card_number", ""))[-4:]
                ab.referencia = f"Orden {ab.orden_compra}"
            else:
                ab.estado, ab.referencia = Abono.Estado.RECHAZADO, "Pago rechazado por Webpay"
        ab.save()
        registrar(ab.registrado_por, f"Pago Webpay {ab.get_estado_display().lower()} ({_clp(ab.monto)})", ab.solicitud.codigo)
    return ab


# --------------------------------------------------------------------------
# Tiempos de una solicitud y cambio de rol
# --------------------------------------------------------------------------
def duracion_texto(delta):
    """timedelta -> «2 d 3 h», «5 h 20 min» o «40 min»."""
    minutos = int(abs(delta.total_seconds()) // 60)
    dias, resto = divmod(minutos, 1440)
    horas, mins = divmod(resto, 60)
    if dias:
        return f"{dias} d {horas} h"
    if horas:
        return f"{horas} h {mins} min" if mins else f"{horas} h"
    return f"{mins} min"


def tiempos_de(s):
    """Lista de (etiqueta, texto) con los tiempos del servicio, para la ficha de la solicitud."""
    ahora = timezone.now()
    filas = [("Recibida hace", duracion_texto(ahora - s.creada))]
    resta = s.ceremonia_inicio - ahora
    filas.append(("Ceremonia", f"en {duracion_texto(resta)}" if resta.total_seconds() > 0 else f"hace {duracion_texto(resta)}"))
    if s.velatorio_inicio and s.velatorio_fin:
        filas.append(("Duración del velatorio", duracion_texto(s.velatorio_fin - s.velatorio_inicio)))
    t = Tarifa.objects.filter(tipo_servicio=s.tipo_servicio).first()
    if t and t.duracion_horas:
        filas.append(("Duración estimada del servicio", f"{t.duracion_horas} h"))
        inicio = s.ceremonia_inicio - timedelta(hours=t.duracion_horas)
        filas.append(("Preparación debería comenzar", timezone.localtime(inicio).strftime("%d/%m %H:%M") + (" (ya pasó)" if inicio < ahora and not s.etapa else "")))
    return filas


@transaction.atomic
def cambiar_rol(usuario, rol, por):
    """Cambia el rol de una persona. Protege la cuenta propia y al último administrador."""
    from .permissions import ADMIN, GRUPO_DE_ROL, GRUPOS, rol_principal
    if rol not in (ADMIN, *GRUPO_DE_ROL):
        raise ValidationError("Rol no válido.")
    if rol_principal(usuario) == rol:
        return usuario
    if usuario.pk == por.pk:
        raise ValidationError("No puedes cambiar tu propio rol.")
    if usuario.is_superuser and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=usuario.pk).exists():
        raise ValidationError("Debe quedar al menos un administrador.")
    usuario.groups.remove(*usuario.groups.filter(name__in=GRUPOS))
    if rol == ADMIN:
        usuario.is_staff = usuario.is_superuser = True
    else:
        usuario.is_staff = usuario.is_superuser = False
        usuario.groups.add(Group.objects.get(name=GRUPO_DE_ROL[rol]))
    usuario.save(update_fields=["is_staff", "is_superuser"])
    registrar(por, f"Rol cambiado a {rol}", usuario.username)
    return usuario
