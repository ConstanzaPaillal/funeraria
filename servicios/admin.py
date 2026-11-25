"""Django Admin personalizado: listados útiles, filtros, búsqueda, inlines y acciones."""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from . import estados, services
from .models import (Actividad, Ausencia, Articulo, Documento, Movimiento, Pedido, PedidoItem, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta,
                     VentaItem)


class SoloLectura:
    """Mixin: registros que se crean solo desde el sistema (no se editan a mano)."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DocumentoInline(admin.TabularInline):
    model = Documento
    extra = 0
    max_num = 3
    can_delete = False


class TrasladoInline(admin.TabularInline):
    model = Traslado
    extra = 0
    fields = ("origen", "destino", "inicio", "duracion_min", "vehiculo", "chofer", "estado")
    show_change_link = True


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ("codigo", "fallecido", "tipo_servicio", "ceremonia_inicio", "etapa_texto", "estado_texto", "responsable")
    list_filter = ("etapa", "tipo_servicio", "responsable", "sala")
    search_fields = ("codigo", "fallecido", "contacto_nombre", "contacto_telefono")
    date_hierarchy = "ceremonia_inicio"
    autocomplete_fields = ("sala",)
    inlines = [DocumentoInline, TrasladoInline]
    actions = ["avanzar"]
    readonly_fields = ("codigo", "aviso_enviado_en")
    list_select_related = ("responsable",)

    @admin.display(description="Etapa")
    def etapa_texto(self, obj):
        return obj.etapa_texto

    @admin.display(description="Estado")
    def estado_texto(self, obj):
        return estados.etiqueta(obj.estado)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("documentos")

    @admin.action(description="Avanzar a la siguiente etapa")
    def avanzar(self, request, queryset):
        for s in queryset:
            try:
                services.avanzar_etapa(s, request.user)
            except ValidationError as e:
                self.message_user(request, f"{s.codigo}: {' '.join(e.messages)}", messages.WARNING)


@admin.register(Sala)
class SalaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "capacidad", "activa")
    list_filter = ("activa",)
    search_fields = ("nombre",)


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "patente", "tipo", "estado")
    list_filter = ("tipo", "estado")
    search_fields = ("nombre", "patente")


@admin.register(Traslado)
class TrasladoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "solicitud", "origen", "destino", "inicio", "vehiculo", "chofer", "estado")
    list_filter = ("estado", "vehiculo", "chofer")
    search_fields = ("origen", "destino", "solicitud__codigo", "solicitud__fallecido")
    date_hierarchy = "inicio"
    list_select_related = ("solicitud", "vehiculo", "chofer")


@admin.register(Articulo)
class ArticuloAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "gama", "precio", "publicado", "stock", "stock_minimo", "estado_texto")
    list_filter = ("categoria",)
    search_fields = ("nombre",)
    readonly_fields = ("stock",)  # el stock solo cambia con entradas/salidas registradas

    @admin.display(description="Estado")
    def estado_texto(self, obj):
        return estados.etiqueta(obj.estado)


@admin.register(Movimiento)
class MovimientoAdmin(SoloLectura, admin.ModelAdmin):
    list_display = ("creado", "articulo", "tipo", "cantidad", "motivo", "usuario")
    list_filter = ("tipo", "articulo")
    search_fields = ("articulo__nombre", "motivo")
    date_hierarchy = "creado"


class PedidoItemInline(admin.TabularInline):
    model = PedidoItem
    extra = 1


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "solicitud", "estado", "creado", "entregado_en")
    list_filter = ("estado",)
    search_fields = ("solicitud__codigo", "solicitud__fallecido")
    inlines = [PedidoItemInline]
    readonly_fields = ("estado", "entregado_en", "entregado_por", "creado_por")
    actions = ["entregar"]

    @admin.action(description="Preparar y entregar (descuenta stock)")
    def entregar(self, request, queryset):
        for p in queryset:
            try:
                services.entregar_pedido(p, request.user)
            except ValidationError as e:
                self.message_user(request, f"{p.codigo}: {' '.join(e.messages)}", messages.ERROR)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(Actividad)
class ActividadAdmin(SoloLectura, admin.ModelAdmin):
    list_display = ("creada", "usuario", "accion", "objeto")
    list_filter = ("usuario",)
    search_fields = ("accion", "objeto")
    date_hierarchy = "creada"


@admin.register(Tarifa)
class TarifaAdmin(admin.ModelAdmin):
    list_display = ("tipo_servicio", "precio")
    list_editable = ("precio",)


class VentaItemInline(admin.TabularInline):
    model = VentaItem
    extra = 0
    can_delete = False
    readonly_fields = ("descripcion", "cantidad", "precio_unitario", "articulo")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Venta)
class VentaAdmin(SoloLectura, admin.ModelAdmin):
    """Las ventas se emiten desde el sistema; aquí solo se consultan."""
    list_display = ("numero", "tipo_documento", "solicitud", "cliente_nombre", "total", "anulada", "creada")
    list_filter = ("tipo_documento", "anulada")
    search_fields = ("numero", "cliente_nombre", "cliente_rut", "solicitud__codigo", "solicitud__fallecido")
    inlines = [VentaItemInline]


@admin.register(Ausencia)
class AusenciaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "tipo", "desde", "hasta", "estado")
    list_filter = ("estado", "tipo")
    search_fields = ("usuario__username", "usuario__first_name", "usuario__last_name")
    date_hierarchy = "desde"
