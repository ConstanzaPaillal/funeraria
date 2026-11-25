from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api, views

router = DefaultRouter()
router.register("solicitudes", api.SolicitudViewSet, basename="api-solicitud")
router.register("documentos", api.DocumentoViewSet, basename="api-documento")
router.register("salas", api.SalaViewSet, basename="api-sala")
router.register("vehiculos", api.VehiculoViewSet, basename="api-vehiculo")
router.register("traslados", api.TrasladoViewSet, basename="api-traslado")
router.register("articulos", api.ArticuloViewSet, basename="api-articulo")
router.register("movimientos", api.MovimientoViewSet, basename="api-movimiento")
router.register("pedidos", api.PedidoViewSet, basename="api-pedido")
router.register("actividad", api.ActividadViewSet, basename="api-actividad")
router.register("tarifas", api.TarifaViewSet, basename="api-tarifa")
router.register("ventas", api.VentaViewSet, basename="api-venta")
router.register("ausencias", api.AusenciaViewSet, basename="api-ausencia")

urlpatterns = [
    path("", views.home, name="home"),
    path("catalogo/", views.landing, name="catalogo"),

    path("solicitudes/", views.SolicitudLista.as_view(), name="solicitud_list"),
    path("solicitudes/exportar/", views.solicitud_exportar, name="solicitud_export"),
    path("solicitudes/nueva/", views.SolicitudCrear.as_view(), name="solicitud_create"),
    path("solicitudes/<int:pk>/", views.SolicitudDetalle.as_view(), name="solicitud_detail"),
    path("solicitudes/<int:pk>/editar/", views.SolicitudEditar.as_view(), name="solicitud_edit"),
    path("solicitudes/<int:pk>/eliminar/", views.SolicitudEliminar.as_view(), name="solicitud_delete"),
    path("solicitudes/<int:pk>/avanzar/", views.solicitud_avanzar, name="solicitud_avanzar"),
    path("solicitudes/<int:pk>/documentos/<int:doc_pk>/", views.documento_marcar, name="documento_marcar"),

    path("salas/", views.sala_agenda, name="sala_agenda"),

    path("traslados/", views.TrasladoLista.as_view(), name="traslado_list"),
    path("traslados/nuevo/", views.TrasladoCrear.as_view(), name="traslado_create"),
    path("traslados/<int:pk>/editar/", views.TrasladoEditar.as_view(), name="traslado_edit"),
    path("traslados/<int:pk>/estado/", views.traslado_estado, name="traslado_estado"),

    path("flota/", views.VehiculoLista.as_view(), name="vehiculo_list"),
    path("flota/nuevo/", views.VehiculoCrear.as_view(), name="vehiculo_create"),
    path("flota/<int:pk>/editar/", views.VehiculoEditar.as_view(), name="vehiculo_edit"),

    path("inventario/", views.ArticuloLista.as_view(), name="articulo_list"),
    path("inventario/nuevo/", views.ArticuloCrear.as_view(), name="articulo_create"),
    path("inventario/<int:pk>/editar/", views.ArticuloEditar.as_view(), name="articulo_edit"),
    path("inventario/<int:pk>/eliminar/", views.ArticuloEliminar.as_view(), name="articulo_delete"),
    path("inventario/<int:pk>/fotos/", views.articulo_fotos, name="articulo_fotos"),
    path("inventario/<int:pk>/fotos/<int:foto_pk>/eliminar/", views.articulo_foto_eliminar, name="articulo_foto_eliminar"),
    path("inventario/movimiento/", views.MovimientoCrear.as_view(), name="movimiento_create"),
    path("inventario/movimientos/", views.MovimientoLista.as_view(), name="movimiento_list"),

    path("pedidos/", views.PedidoLista.as_view(), name="pedido_list"),
    path("pedidos/nuevo/", views.pedido_crear, name="pedido_create"),
    path("pedidos/<int:pk>/entregar/", views.pedido_entregar, name="pedido_entregar"),

    path("usuarios/", views.UsuarioLista.as_view(), name="usuario_list"),
    path("usuarios/nuevo/", views.UsuarioCrear.as_view(), name="usuario_create"),
    path("usuarios/<int:pk>/activar/", views.usuario_toggle, name="usuario_toggle"),
    path("usuarios/<int:pk>/editar/", views.usuario_editar, name="usuario_edit"),
    path("perfil/", views.mi_perfil, name="perfil"),
    path("perfil/clave/", views.CambiarClave.as_view(), name="perfil_clave"),
    path("etiquetas/", views.EtiquetaLista.as_view(), name="etiqueta_list"),
    path("etiquetas/nueva/", views.EtiquetaCrear.as_view(), name="etiqueta_create"),
    path("etiquetas/<int:pk>/editar/", views.EtiquetaEditar.as_view(), name="etiqueta_edit"),
    path("etiquetas/<int:pk>/eliminar/", views.etiqueta_eliminar, name="etiqueta_delete"),
    path("solicitudes/<int:pk>/abonos/nuevo/", views.abono_crear, name="abono_create"),
    path("solicitudes/<int:pk>/abonos/webpay/", views.webpay_iniciar, name="webpay_iniciar"),
    path("abonos/<int:pk>/anular/", views.abono_anular, name="abono_anular"),
    path("pagos/webpay/retorno/", views.webpay_retorno, name="webpay_retorno"),
    path("pagos/<int:pk>/", views.pago_resultado, name="pago_resultado"),
    path("actividad/", views.ActividadLista.as_view(), name="actividad_list"),

    path("calendario/", views.calendario, name="calendario"),
    path("precios/", views.precios, name="precios"),
    path("precios/tarifas/", views.tarifas_editar, name="tarifas_editar"),

    path("ventas/", views.VentaLista.as_view(), name="venta_list"),
    path("solicitudes/<int:pk>/vender/", views.venta_crear, name="venta_create"),
    path("ventas/<int:pk>/", views.venta_detalle, name="venta_detail"),
    path("ventas/<int:pk>/pdf/", views.venta_pdf, name="venta_pdf"),
    path("ventas/<int:pk>/anular/", views.venta_anular, name="venta_anular"),

    path("ausencias/", views.AusenciaLista.as_view(), name="ausencia_list"),
    path("ausencias/mias/", views.ausencia_mias, name="ausencia_mias"),
    path("ausencias/nueva/", views.ausencia_crear, name="ausencia_create"),
    path("ausencias/<int:pk>/resolver/", views.ausencia_resolver, name="ausencia_resolver"),
    path("ausencias/<int:pk>/cancelar/", views.ausencia_cancelar, name="ausencia_cancelar"),

    path("familia/", views.familia, name="familia"),
    path("api/", include(router.urls)),
]
