"""
API REST (DRF) de Cristo Rey. Cada ViewSet declara qué roles pueden leer y escribir;
la clase PermisoPorRol (settings.REST_FRAMEWORK) aplica la regla.
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import exception_handler

from . import serializers as sz
from . import services
from .models import Actividad, Ausencia, Articulo, Documento, Movimiento, Pedido, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta
from .permissions import BODEGA, CHOFER, RECEPCION, TODOS, tiene_rol


def manejador(exc, context):
    """Convierte los errores de validación de Django (reglas de negocio) en respuestas 400."""
    if isinstance(exc, DjangoValidationError):
        detalle = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
        exc = serializers.ValidationError(detalle)
    elif isinstance(exc, DjangoPermissionDenied):
        exc = PermissionDenied()
    return exception_handler(exc, context)


class SolicitudViewSet(viewsets.ModelViewSet):
    queryset = Solicitud.objects.select_related("responsable", "sala").prefetch_related("documentos")
    serializer_class = sz.SolicitudSerializer
    roles_lectura = {RECEPCION}
    roles_escritura = {RECEPCION}

    def perform_create(self, serializer):
        obj = serializer.save(responsable=serializer.validated_data.get("responsable") or self.request.user)
        services.registrar(self.request.user, "Solicitud creada (API)", obj.codigo)

    def perform_destroy(self, instance):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Solo el administrador puede eliminar solicitudes.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def avanzar(self, request, pk=None):
        s = services.avanzar_etapa(self.get_object(), request.user)
        return Response(self.get_serializer(s).data)


class DocumentoViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Solo se marca como entregado o pendiente (los documentos nacen con la solicitud)."""
    queryset = Documento.objects.select_related("solicitud")
    serializer_class = sz.DocumentoSerializer
    http_method_names = ["get", "patch", "head", "options"]
    roles_lectura = {RECEPCION}
    roles_escritura = {RECEPCION}

    def perform_update(self, serializer):
        services.marcar_documento(serializer.instance, serializer.validated_data.get("entregado", serializer.instance.entregado), self.request.user)


class SalaViewSet(viewsets.ModelViewSet):
    queryset = Sala.objects.all()
    serializer_class = sz.SalaSerializer
    roles_lectura = {RECEPCION}
    roles_escritura = set()  # solo administrador


class VehiculoViewSet(viewsets.ModelViewSet):
    queryset = Vehiculo.objects.all()
    serializer_class = sz.VehiculoSerializer
    roles_lectura = {RECEPCION}
    roles_escritura = set()


class TrasladoViewSet(viewsets.ModelViewSet):
    queryset = Traslado.objects.select_related("solicitud", "vehiculo", "chofer")
    serializer_class = sz.TrasladoSerializer
    roles_lectura = {RECEPCION, CHOFER}
    roles_escritura = {RECEPCION, CHOFER}
    acciones_chofer = {"list", "retrieve", "estado"}

    def _es_solo_chofer(self):
        return not tiene_rol(self.request.user, {RECEPCION})

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self._es_solo_chofer() and self.action not in self.acciones_chofer:
            raise PermissionDenied("Un chofer solo puede ver sus traslados y actualizar su estado.")

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(chofer=self.request.user) if self._es_solo_chofer() else qs

    @action(detail=True, methods=["post"])
    def estado(self, request, pk=None):
        nuevo = {"iniciar": "en_camino", "retraso": "retrasado", "completar": "completado"}.get(request.data.get("accion"))
        if not nuevo:
            raise serializers.ValidationError({"accion": "Usa iniciar, retraso o completar."})
        t = services.cambiar_estado_traslado(self.get_object(), nuevo, request.user)
        return Response(self.get_serializer(t).data)


class ArticuloViewSet(viewsets.ModelViewSet):
    queryset = Articulo.objects.all()
    serializer_class = sz.ArticuloSerializer
    roles_lectura = {BODEGA, RECEPCION}
    roles_escritura = {BODEGA}


class MovimientoViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Movimiento.objects.select_related("articulo", "usuario")
    serializer_class = sz.MovimientoSerializer
    roles_lectura = {BODEGA}
    roles_escritura = {BODEGA}

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = services.registrar_movimiento(d["articulo"].pk, d["tipo"], d["cantidad"], self.request.user, d.get("motivo", ""))


class PedidoViewSet(viewsets.ModelViewSet):
    queryset = Pedido.objects.select_related("solicitud").prefetch_related("items")
    serializer_class = sz.PedidoSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]
    roles_lectura = {BODEGA, RECEPCION}
    roles_escritura = {BODEGA}

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)

    def perform_destroy(self, instance):
        if instance.estado == Pedido.Estado.ENTREGADO:
            raise serializers.ValidationError("No se puede eliminar un pedido ya entregado.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def entregar(self, request, pk=None):
        p = services.entregar_pedido(self.get_object(), request.user)
        return Response(self.get_serializer(p).data)


class ActividadViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Actividad.objects.select_related("usuario")
    serializer_class = sz.ActividadSerializer
    roles_lectura = set()  # solo administrador
    roles_escritura = set()


class TarifaViewSet(viewsets.ReadOnlyModelViewSet):
    """Precios de los servicios (solo lectura; se editan desde Precios o el Admin)."""
    queryset = Tarifa.objects.all()
    serializer_class = sz.TarifaSerializer
    roles_lectura = {RECEPCION, BODEGA}
    roles_escritura = set()


class VentaViewSet(viewsets.ReadOnlyModelViewSet):
    """Boletas y facturas emitidas (solo lectura). Se emiten desde la solicitud."""
    queryset = Venta.objects.select_related("solicitud").prefetch_related("items")
    serializer_class = sz.VentaSerializer
    roles_lectura = {RECEPCION}
    roles_escritura = set()


class AusenciaViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Cada persona ve y pide las suyas; el administrador ve todas y las resuelve."""
    queryset = Ausencia.objects.select_related("usuario")
    serializer_class = sz.AusenciaSerializer
    roles_lectura = TODOS
    roles_escritura = {RECEPCION, BODEGA, CHOFER}

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if self.request.user.is_superuser else qs.filter(usuario=self.request.user)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        aus = services.solicitar_ausencia(request.user, ser.validated_data)
        return Response(self.get_serializer(aus).data, status=201)

    def perform_destroy(self, instance):
        services.cancelar_ausencia(instance, self.request.user)

    @action(detail=True, methods=["post"])
    def resolver(self, request, pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied("Solo el administrador resuelve ausencias.")
        a = services.resolver_ausencia(self.get_object(), request.data.get("accion", ""), request.user, request.data.get("comentario", ""))
        return Response(self.get_serializer(a).data)
