"""Serializadores de la API REST. Validan con las mismas reglas del modelo que usan las pantallas."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (Actividad, Ausencia, Articulo, Documento, Movimiento, Pedido, PedidoItem, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta,
                     VentaItem)


class ValidaConModeloMixin:
    """Ejecuta Model.clean() antes de guardar para devolver 400 con el mensaje en vez de un error de servidor."""
    def validate(self, attrs):
        attrs = super().validate(attrs)
        obj = self.instance
        if obj is None:
            obj = self.Meta.model()
        else:
            obj = type(obj).objects.get(pk=obj.pk)
        for k, v in attrs.items():
            setattr(obj, k, v)
        try:
            obj.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, "error_dict") else e.messages)
        return attrs


class DocumentoSerializer(serializers.ModelSerializer):
    nombre = serializers.CharField(source="get_tipo_display", read_only=True)

    class Meta:
        model = Documento
        fields = ["id", "solicitud", "tipo", "nombre", "entregado", "entregado_en"]
        read_only_fields = ["solicitud", "tipo", "entregado_en"]


class SolicitudSerializer(ValidaConModeloMixin, serializers.ModelSerializer):
    estado = serializers.CharField(read_only=True)
    etapa_texto = serializers.CharField(read_only=True)
    documentos = DocumentoSerializer(many=True, read_only=True)

    class Meta:
        model = Solicitud
        fields = ["id", "codigo", "fallecido", "contacto_nombre", "contacto_telefono", "contacto_email", "tipo_servicio",
                  "ceremonia_inicio", "lugar_ceremonia", "feretro", "sala", "velatorio_inicio", "velatorio_fin", "responsable",
                  "etapa", "etapa_texto", "estado", "documentos", "creada"]
        read_only_fields = ["codigo", "etapa", "creada"]  # la etapa solo avanza con la acción "avanzar"

    def validate_ceremonia_inicio(self, valor):
        from django.utils import timezone
        if self.instance is None and valor < timezone.now():
            raise serializers.ValidationError("La ceremonia debe ser en una fecha y hora futuras.")
        return valor


class SalaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sala
        fields = ["id", "nombre", "capacidad", "activa"]


class VehiculoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehiculo
        fields = ["id", "nombre", "patente", "tipo", "estado"]


class TrasladoSerializer(ValidaConModeloMixin, serializers.ModelSerializer):
    codigo = serializers.CharField(read_only=True)

    class Meta:
        model = Traslado
        fields = ["id", "codigo", "solicitud", "origen", "destino", "inicio", "duracion_min", "fin", "vehiculo", "chofer", "estado"]
        read_only_fields = ["fin", "estado"]  # el estado cambia solo con la acción "estado"


class ArticuloSerializer(serializers.ModelSerializer):
    estado = serializers.CharField(read_only=True)

    class Meta:
        model = Articulo
        fields = ["id", "nombre", "categoria", "gama", "precio", "descripcion", "publicado", "unidad", "stock", "stock_minimo", "estado"]
        read_only_fields = ["stock"]  # el stock cambia solo con movimientos


class MovimientoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Movimiento
        fields = ["id", "articulo", "tipo", "cantidad", "motivo", "usuario", "creado"]
        read_only_fields = ["usuario", "creado"]


class PedidoItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedidoItem
        fields = ["articulo", "cantidad"]


class PedidoSerializer(serializers.ModelSerializer):
    items = PedidoItemSerializer(many=True)
    codigo = serializers.CharField(read_only=True)

    class Meta:
        model = Pedido
        fields = ["id", "codigo", "solicitud", "estado", "items", "creado", "entregado_en"]
        read_only_fields = ["estado", "creado", "entregado_en"]

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("El pedido necesita al menos un material.")
        ids = [i["articulo"].pk for i in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("No repitas el mismo material en un pedido.")
        return items

    def create(self, datos):
        items = datos.pop("items")
        pedido = Pedido.objects.create(**datos)
        PedidoItem.objects.bulk_create([PedidoItem(pedido=pedido, **i) for i in items])
        return pedido


class ActividadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Actividad
        fields = ["id", "usuario", "accion", "objeto", "creada"]


class TarifaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tarifa
        fields = ["id", "tipo_servicio", "precio"]


class VentaItemSerializer(serializers.ModelSerializer):
    subtotal = serializers.IntegerField(read_only=True)

    class Meta:
        model = VentaItem
        fields = ["descripcion", "cantidad", "precio_unitario", "subtotal"]


class VentaSerializer(serializers.ModelSerializer):
    items = VentaItemSerializer(many=True, read_only=True)

    class Meta:
        model = Venta
        fields = ["id", "numero", "tipo_documento", "solicitud", "cliente_nombre", "cliente_rut", "cliente_giro", "cliente_direccion",
                  "forma_pago", "neto", "iva", "total", "anulada", "creada_por", "creada", "items"]
        read_only_fields = fields


class AusenciaSerializer(serializers.ModelSerializer):
    persona = serializers.SerializerMethodField()
    dias = serializers.IntegerField(read_only=True)

    class Meta:
        model = Ausencia
        fields = ["id", "usuario", "persona", "tipo", "desde", "hasta", "dias", "motivo", "estado", "comentario", "resuelta_en", "creada"]
        read_only_fields = ["usuario", "estado", "comentario", "resuelta_en", "creada"]  # solo cambian con la decisión del administrador

    def get_persona(self, obj):
        return obj.usuario.get_full_name() or obj.usuario.username
