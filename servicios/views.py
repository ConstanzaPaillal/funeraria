"""
Vistas con templates de Cristo Rey.

Todas exigen sesión iniciada y un rol autorizado (ver permissions.py), salvo la consulta pública
de la familia, que exige código + verificación y limita los intentos por IP.
"""
import calendar as _cal
from datetime import date, datetime, time, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from urllib.parse import urlencode

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView

from . import forms, services
from .estados import ORDEN_SOLICITUD, etiqueta
from .models import (Abono, Actividad, Articulo, ArticuloFoto, Ausencia, Etiqueta, Perfil, Documento, Movimiento, Pedido, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta)
from .permissions import ADMIN, BODEGA, CHOFER, RECEPCION, RolesMixin, rol_principal, roles_de, tiene_rol
from . import graficos
from .presentacion import MENSAJES_FAMILIA, etapas_de


def rol_requerido(*roles):
    """Decorador para vistas basadas en funciones: sin rol autorizado -> 403."""
    def deco(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *a, **kw):
            if not tiene_rol(request.user, roles):
                raise PermissionDenied
            return vista(request, *a, **kw)
        return envoltura
    return deco


def avisar_errores(request, error):
    for m in error.messages:
        messages.error(request, m)


def rango_hoy():
    hoy = timezone.localdate()
    ini = timezone.make_aware(datetime.combine(hoy, time.min))
    return ini, ini + timedelta(days=1)


# ==========================================================================
# Panel de inicio (según el rol)
# ==========================================================================
def _atencion(solicitudes):
    lista = [s for s in solicitudes if s.estado in ("urgente", "docs_pendientes")]
    return sorted(lista, key=lambda s: (ORDEN_SOLICITUD[s.estado], s.ceremonia_inicio))


def landing(request):
    """Sitio público: catálogo de féretros y urnas con sus precios (sin mostrar stock ni datos internos)."""
    pub = Articulo.objects.filter(publicado=True, precio__gt=0).prefetch_related("fotos")
    return render(request, "servicios/landing.html", {
        "feretros": pub.filter(categoria="feretros").order_by("precio"),
        "urnas": pub.filter(categoria="urnas").order_by("precio"),
        "tarifas": Tarifa.objects.filter(precio__gt=0, activo=True).order_by("precio"),
        "empresa": settings.EMPRESA})


def home(request):
    if not request.user.is_authenticated:
        return landing(request)
    rol = rol_principal(request.user)
    ctx = {"panel": rol or "sin_rol"}
    if rol is None:
        return render(request, "servicios/home.html", ctx)
    ini, fin = rango_hoy()
    ahora = timezone.now()
    todas = list(Solicitud.objects.prefetch_related("documentos").select_related("responsable"))
    activas = [s for s in todas if not s.finalizada]
    alertas = [a for a in Articulo.objects.all() if a.estado != "stock_normal"]
    alertas.sort(key=lambda a: a.stock / max(a.stock_minimo, 1))
    if rol == ADMIN:
        hoy = list(Traslado.objects.filter(inicio__gte=ini, inicio__lt=fin).select_related("vehiculo", "chofer", "solicitud"))
        hoy_local = timezone.localdate()
        pendientes_aus = list(Ausencia.objects.filter(estado=Ausencia.Estado.PENDIENTE).select_related("usuario").order_by("desde"))
        ausentes = list(services.ausentes_en(hoy_local))
        ctx.update(
            ausencias_pendientes=pendientes_aus, ausentes_hoy=ausentes,
            ausencias_proximas=list(Ausencia.objects.filter(estado=Ausencia.Estado.APROBADA, desde__gt=hoy_local,
                                                           desde__lte=hoy_local + timedelta(days=14)).select_related("usuario").order_by("desde")[:6]),
            stats=[(len(activas), "Solicitudes activas"),
                   (sum(1 for s in activas if s.ceremonia_inicio - ahora < timedelta(hours=72)), "Ceremonias en 72 horas"),
                   (len(alertas), "Artículos en alerta"), (len(hoy), "Traslados hoy"),
                   (len(ausentes), "Personas ausentes hoy"), (len(pendientes_aus), "Permisos por revisar")],
            atencion=_atencion(activas), alertas=alertas, traslados_hoy=hoy,
            graficos=[graficos.por_estado(todas), graficos.ingresos_mensuales(), graficos.servicios_por_tipo(todas),
                      graficos.ceremonias_proximas(activas)])
    elif rol == RECEPCION:
        mias = [s for s in activas if s.responsable_id == request.user.pk]
        ctx.update(
            stats=[(len(mias), "Mis solicitudes activas"), (len(_atencion(mias)), "Requieren atención"),
                   (sum(1 for s in mias if s.ceremonia_inicio - ahora < timedelta(hours=72)), "Ceremonias en 72 horas")],
            atencion=_atencion(mias), proximas=sorted(mias, key=lambda s: s.ceremonia_inicio),
            graficos=[graficos.etapas_activas(mias), graficos.ceremonias_proximas(mias)])
    elif rol == BODEGA:
        pendientes = list(Pedido.objects.filter(estado=Pedido.Estado.PENDIENTE).select_related("solicitud"))
        ctx.update(stats=[(Articulo.objects.count(), "Artículos en catálogo"), (len(alertas), "Artículos en alerta"),
                          (len(pendientes), "Pedidos pendientes")], alertas=alertas, pendientes=pendientes,
                   graficos=[graficos.stock_vs_minimo(), graficos.salidas_semana()])
    else:
        mios = list(Traslado.objects.filter(chofer=request.user, inicio__gte=ini, inicio__lt=fin).select_related("vehiculo", "solicitud"))
        ctx.update(stats=[(len(mios), "Traslados hoy"), (sum(t.estado == "completado" for t in mios), "Completados"),
                          (sum(t.estado != "completado" for t in mios), "Por realizar")], traslados=mios,
                   graficos=[graficos.traslados_semana(request.user)])
    return render(request, "servicios/home.html", ctx)


# ==========================================================================
# Solicitudes
# ==========================================================================
class SolicitudLista(RolesMixin, ListView):
    roles_permitidos = {RECEPCION}
    template_name = "servicios/solicitud_list.html"
    paginate_by = 15

    def get_queryset(self):
        return services.buscar_solicitudes(self.request.GET)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        filtros = {k: g.get(k, "") for k in services.FILTROS}
        params = {k: v for k, v in filtros.items() if v}
        ctx.update(
            f=filtros, hay_filtros=bool(params), total=len(self.object_list),
            querystring=urlencode(params),
            estados=[(k, etiqueta(k)) for k in ("en_curso", "docs_pendientes", "urgente", "finalizada")],
            servicios=Solicitud.Servicio.choices,
            responsables=forms.usuarios_con_rol(RECEPCION), etiquetas=Etiqueta.objects.all())
        return ctx


@rol_requerido(RECEPCION)
def solicitud_exportar(request):
    """Descarga en Excel exactamente lo que muestra el buscador con los filtros activos."""
    from .exportacion import libro_solicitudes
    lista = services.buscar_solicitudes(request.GET)
    contenido = libro_solicitudes(lista)
    services.registrar(request.user, f"Descarga Excel de solicitudes ({len(lista)})", "")
    resp = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="cristorey_solicitudes_{timezone.localdate():%Y%m%d}.xlsx"'
    return resp


class FormularioMixin(SuccessMessageMixin):
    template_name = "servicios/form.html"
    titulo = ""
    volver = reverse_lazy("home")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(titulo=self.titulo, cancelar_url=self.volver)
        return ctx


class SolicitudCrear(RolesMixin, FormularioMixin, CreateView):
    roles_permitidos = {RECEPCION}
    model = Solicitud
    form_class = forms.SolicitudForm
    titulo = "Nueva solicitud de servicio"
    volver = reverse_lazy("solicitud_list")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["user"] = self.request.user
        return kw

    def form_valid(self, form):
        resp = super().form_valid(form)
        services.registrar(self.request.user, "Solicitud creada", self.object.codigo)
        messages.success(self.request, f"Solicitud {self.object.codigo} creada. Faltan los 3 documentos de la familia.")
        return resp

    def get_success_url(self):
        return reverse("solicitud_detail", args=[self.object.pk])


class SolicitudEditar(RolesMixin, FormularioMixin, UpdateView):
    roles_permitidos = {RECEPCION}
    model = Solicitud
    form_class = forms.SolicitudForm
    titulo = "Editar solicitud"
    success_message = "Los cambios se guardaron."

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["user"] = self.request.user
        return kw

    def form_valid(self, form):
        resp = super().form_valid(form)
        services.registrar(self.request.user, "Solicitud editada", self.object.codigo)
        return resp

    def get_success_url(self):
        return reverse("solicitud_detail", args=[self.object.pk])

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["cancelar_url"] = self.get_success_url()
        return ctx


class SolicitudDetalle(RolesMixin, DetailView):
    roles_permitidos = {RECEPCION}
    model = Solicitud
    template_name = "servicios/solicitud_detail.html"

    def get_queryset(self):
        return Solicitud.objects.select_related("responsable", "sala", "feretro").prefetch_related(
            "documentos", "traslados__vehiculo", "traslados__chofer", "pedidos", "ventas", "abonos", "etiquetas")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        s = self.object
        services.anotar_precios([s])
        ctx.update(etapas=etapas_de(s), siguiente=(Solicitud.Etapa(s.etapa + 1).label if not s.finalizada else None),
                   tiempos=services.tiempos_de(s), abono_form=forms.AbonoForm(), webpay_form=forms.MontoWebpayForm(initial={"monto": s.saldo or None}))
        return ctx


class SolicitudEliminar(RolesMixin, DeleteView):
    roles_permitidos = set()  # solo administrador
    model = Solicitud
    template_name = "servicios/confirm_delete.html"
    success_url = reverse_lazy("solicitud_list")

    def form_valid(self, form):
        try:
            nombre = str(self.object)
            resp = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, "No se puede eliminar: la solicitud tiene traslados o pedidos asociados.")
            return redirect("solicitud_detail", pk=self.object.pk)
        services.registrar(self.request.user, "Solicitud eliminada", nombre)
        messages.success(self.request, f"«{nombre}» fue eliminada.")
        return resp


@require_POST
@rol_requerido(RECEPCION)
def documento_marcar(request, pk, doc_pk):
    doc = get_object_or_404(Documento, pk=doc_pk, solicitud_id=pk)
    entregado = request.POST.get("entregado") == "1"
    services.marcar_documento(doc, entregado, request.user)
    messages.success(request, f"«{doc.get_tipo_display()}»: {'entregado' if entregado else 'pendiente'}.")
    return redirect("solicitud_detail", pk=pk)


@require_POST
@rol_requerido(RECEPCION)
def solicitud_avanzar(request, pk):
    s = get_object_or_404(Solicitud, pk=pk)
    try:
        s = services.avanzar_etapa(s, request.user)
        messages.success(request, f"Solicitud {s.codigo}: ahora en «{s.etapa_texto}».")
    except ValidationError as e:
        for m in e.messages:
            messages.warning(request, m)
    return redirect("solicitud_detail", pk=pk)


# ==========================================================================
# Salas
# ==========================================================================
@rol_requerido(RECEPCION)
def sala_agenda(request):
    ahora = timezone.now()
    salas = Sala.objects.filter(activa=True)
    reservas = Solicitud.objects.filter(sala__in=salas, velatorio_fin__gte=ahora).exclude(etapa=Solicitud.Etapa.FINALIZADO).order_by("velatorio_inicio")
    por_sala = {s: [] for s in salas}
    for r in reservas:
        por_sala[r.sala].append(r)
    return render(request, "servicios/salas.html", {"por_sala": por_sala})


# ==========================================================================
# Traslados y flota
# ==========================================================================
class TrasladoLista(RolesMixin, ListView):
    roles_permitidos = {RECEPCION, CHOFER}
    template_name = "servicios/traslado_list.html"
    paginate_by = 20

    def get_queryset(self):
        qs = Traslado.objects.select_related("solicitud", "vehiculo", "chofer")
        if not tiene_rol(self.request.user, {RECEPCION}):  # un chofer solo ve lo suyo
            qs = qs.filter(chofer=self.request.user)
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["solo_propios"] = not tiene_rol(self.request.user, {RECEPCION})
        return ctx


class TrasladoCrear(RolesMixin, FormularioMixin, CreateView):
    roles_permitidos = {RECEPCION}
    model = Traslado
    form_class = forms.TrasladoForm
    titulo = "Nuevo traslado"
    volver = reverse_lazy("traslado_list")
    success_url = reverse_lazy("traslado_list")
    success_message = "Traslado programado."

    def form_valid(self, form):
        resp = super().form_valid(form)
        services.registrar(self.request.user, f"Traslado {self.object.codigo} programado", self.object.solicitud.codigo)
        return resp


class TrasladoEditar(RolesMixin, FormularioMixin, UpdateView):
    roles_permitidos = {RECEPCION}
    model = Traslado
    form_class = forms.TrasladoForm
    titulo = "Editar traslado"
    volver = reverse_lazy("traslado_list")
    success_url = reverse_lazy("traslado_list")
    success_message = "Los cambios se guardaron."


@require_POST
@rol_requerido(RECEPCION, CHOFER)
def traslado_estado(request, pk):
    t = get_object_or_404(Traslado, pk=pk)
    if not tiene_rol(request.user, {RECEPCION}) and t.chofer_id != request.user.pk:
        raise PermissionDenied
    nuevo = {"iniciar": "en_camino", "retraso": "retrasado", "completar": "completado"}.get(request.POST.get("accion"))
    if not nuevo:
        messages.error(request, "Acción no válida.")
    else:
        try:
            t = services.cambiar_estado_traslado(t, nuevo, request.user)
            tipo = messages.WARNING if nuevo == "retrasado" else messages.SUCCESS
            messages.add_message(request, tipo, f"Traslado {t.codigo}: {t.get_estado_display().lower()}.")
        except ValidationError as e:
            avisar_errores(request, e)
    return redirect(request.POST.get("next") or "traslado_list")


class VehiculoLista(RolesMixin, ListView):
    roles_permitidos = set()
    model = Vehiculo
    template_name = "servicios/vehiculo_list.html"


class VehiculoCrear(RolesMixin, FormularioMixin, CreateView):
    roles_permitidos = set()
    model = Vehiculo
    form_class = forms.VehiculoForm
    titulo = "Nuevo vehículo"
    volver = reverse_lazy("vehiculo_list")
    success_url = reverse_lazy("vehiculo_list")
    success_message = "Vehículo creado."


class VehiculoEditar(RolesMixin, FormularioMixin, UpdateView):
    roles_permitidos = set()
    model = Vehiculo
    form_class = forms.VehiculoForm
    titulo = "Editar vehículo"
    volver = reverse_lazy("vehiculo_list")
    success_url = reverse_lazy("vehiculo_list")
    success_message = "Los cambios se guardaron."


# ==========================================================================
# Bodega
# ==========================================================================
class ArticuloLista(RolesMixin, ListView):
    roles_permitidos = {BODEGA}
    model = Articulo
    template_name = "servicios/articulo_list.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["en_alerta"] = sum(a.estado != "stock_normal" for a in self.object_list)
        return ctx


class ArticuloCrear(RolesMixin, FormularioMixin, CreateView):
    roles_permitidos = {BODEGA}
    model = Articulo
    form_class = forms.ArticuloForm
    titulo = "Nuevo artículo"
    volver = reverse_lazy("articulo_list")
    success_url = reverse_lazy("articulo_list")
    success_message = "Artículo creado."


class ArticuloEditar(RolesMixin, FormularioMixin, UpdateView):
    roles_permitidos = {BODEGA}
    model = Articulo
    form_class = forms.ArticuloForm
    titulo = "Editar artículo"
    volver = reverse_lazy("articulo_list")
    success_url = reverse_lazy("articulo_list")
    success_message = "Los cambios se guardaron."


class MovimientoCrear(RolesMixin, FormularioMixin, FormView):
    roles_permitidos = {BODEGA}
    form_class = forms.MovimientoForm
    titulo = "Registrar movimiento de bodega"
    volver = reverse_lazy("articulo_list")
    success_url = reverse_lazy("articulo_list")

    def get_initial(self):
        ini = {}
        if self.request.GET.get("articulo", "").isdigit():
            ini["articulo"] = self.request.GET["articulo"]
        if self.request.GET.get("tipo") in ("entrada", "salida"):
            ini["tipo"] = self.request.GET["tipo"]
        return ini

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            mov = services.registrar_movimiento(d["articulo"].pk, d["tipo"], d["cantidad"], self.request.user, d["motivo"])
        except ValidationError as e:
            form.add_error("cantidad", e.messages[0])
            return self.form_invalid(form)
        art = Articulo.objects.get(pk=d["articulo"].pk)
        nivel = {"stock_critico": messages.ERROR, "stock_bajo": messages.WARNING}.get(art.estado, messages.SUCCESS)
        messages.add_message(self.request, nivel, f"{mov.get_tipo_display()} registrada: {mov.cantidad} × {art.nombre}. Stock actual: {art.stock}.")
        return redirect(self.success_url)


class MovimientoLista(RolesMixin, ListView):
    roles_permitidos = {BODEGA}
    template_name = "servicios/movimiento_list.html"
    paginate_by = 25

    def get_queryset(self):
        return Movimiento.objects.select_related("articulo", "usuario")


class PedidoLista(RolesMixin, ListView):
    roles_permitidos = {BODEGA}
    template_name = "servicios/pedido_list.html"
    paginate_by = 20

    def get_queryset(self):
        return Pedido.objects.select_related("solicitud").prefetch_related("items__articulo")


@rol_requerido(BODEGA)
def pedido_crear(request):
    form = forms.PedidoForm(request.POST or None)
    formset = forms.PedidoItemFormSet(request.POST or None, instance=Pedido())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            pedido = form.save(commit=False)
            pedido.creado_por = request.user
            pedido.save()
            formset.instance = pedido
            formset.save()
        services.registrar(request.user, f"Pedido {pedido.codigo} creado", pedido.solicitud.codigo)
        messages.success(request, f"Pedido {pedido.codigo} creado.")
        return redirect("pedido_list")
    return render(request, "servicios/pedido_form.html", {"form": form, "formset": formset, "titulo": "Nuevo pedido de materiales"})


@require_POST
@rol_requerido(BODEGA)
def pedido_entregar(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk)
    try:
        services.entregar_pedido(pedido, request.user)
        messages.success(request, f"Pedido {pedido.codigo} entregado. El stock se actualizó.")
    except ValidationError as e:
        avisar_errores(request, e)
    return redirect("pedido_list")


# ==========================================================================
# Usuarios y actividad (solo administrador)
# ==========================================================================
class UsuarioLista(RolesMixin, ListView):
    roles_permitidos = set()
    template_name = "servicios/usuario_list.html"

    def get_queryset(self):
        usuarios = list(User.objects.order_by("first_name", "username").prefetch_related("groups"))
        for u in usuarios:
            u.rol_texto = {"admin": "Administrador", "recepcion": "Recepción", "bodega": "Bodega", "chofer": "Chofer"}.get(rol_principal(u), "Sin rol")
        return usuarios


class UsuarioCrear(RolesMixin, FormularioMixin, CreateView):
    roles_permitidos = set()
    model = User
    form_class = forms.UsuarioForm
    titulo = "Nuevo usuario"
    volver = reverse_lazy("usuario_list")
    success_url = reverse_lazy("usuario_list")

    def form_valid(self, form):
        resp = super().form_valid(form)
        services.registrar(self.request.user, f"Usuario creado ({form.cleaned_data['rol']})", self.object.username)
        messages.success(self.request, f"Usuario «{self.object.username}» creado.")
        return resp


@require_POST
@rol_requerido()
def usuario_toggle(request, pk):
    u = get_object_or_404(User, pk=pk)
    if u.pk == request.user.pk:
        messages.error(request, "No puedes desactivar tu propia cuenta.")
    else:
        u.is_active = not u.is_active
        u.save(update_fields=["is_active"])
        services.registrar(request.user, "Usuario activado" if u.is_active else "Usuario desactivado", u.username)
        messages.success(request, f"{u.get_full_name() or u.username} fue {'activado' if u.is_active else 'desactivado'}.")
    return redirect("usuario_list")


class ActividadLista(RolesMixin, ListView):
    roles_permitidos = set()
    template_name = "servicios/actividad_list.html"
    paginate_by = 30

    def get_queryset(self):
        return Actividad.objects.select_related("usuario")


# ==========================================================================
# Vista pública de la familia
# ==========================================================================
def familia(request):
    form = forms.FamiliaForm(request.POST or None)
    if request.method == "POST":
        if services.intentos_agotados(request):
            messages.error(request, "Demasiados intentos. Espera unos minutos o llama a recepción.")
        elif form.is_valid():
            s = services.verificar_familia(form.cleaned_data["codigo"], form.cleaned_data["verificacion"])
            if s:
                return render(request, "servicios/familia_detalle.html", {
                    "s": s, "etapas": etapas_de(s), "mensaje": MENSAJES_FAMILIA[s.estado]})
            services.sumar_intento(request)
            messages.error(request, "No encontramos un servicio con esos datos. Revise el código y los últimos 4 dígitos del teléfono.")
    return render(request, "servicios/familia_form.html", {"form": form})


# ==========================================================================
# Calendario
# ==========================================================================
TIPOS_CALENDARIO = (("todo", "Todo"), ("ceremonia", "Ceremonias"), ("velatorio", "Velatorios"), ("traslado", "Traslados"), ("ausencia", "Ausencias"))


def _mes_de(texto):
    try:
        a, m = (int(x) for x in (texto or "").split("-"))
        return date(a, m, 1)
    except (ValueError, TypeError):
        return timezone.localdate().replace(day=1)


def _mover(primero, delta):
    m = primero.month - 1 + delta
    return date(primero.year + m // 12, m % 12 + 1, 1)


@rol_requerido(RECEPCION, CHOFER)
def calendario(request):
    """Calendario mensual. Recepción y administrador ven todo; un chofer ve solo sus traslados."""
    primero = _mes_de(request.GET.get("mes"))
    solo_chofer = not tiene_rol(request.user, {RECEPCION})
    tipo = request.GET.get("tipo", "todo")
    if tipo not in dict(TIPOS_CALENDARIO) or solo_chofer:
        tipo = "traslado" if solo_chofer else "todo"
    semanas = _cal.Calendar(firstweekday=0).monthdatescalendar(primero.year, primero.month)
    ini = timezone.make_aware(datetime.combine(semanas[0][0], time.min))
    fin = timezone.make_aware(datetime.combine(semanas[-1][-1], time.min)) + timedelta(days=1)
    eventos = {d: [] for sem in semanas for d in sem}

    def poner(dia, hora, clase, rotulo, texto, url):
        if dia in eventos:
            eventos[dia].append({"hora": hora, "clase": clase, "rotulo": rotulo, "texto": texto, "url": url})

    if tipo in ("todo", "ceremonia") and not solo_chofer:
        for s in Solicitud.objects.filter(ceremonia_inicio__gte=ini, ceremonia_inicio__lt=fin):
            f = timezone.localtime(s.ceremonia_inicio)
            poner(f.date(), f.strftime("%H:%M"), "ceremonia", "Ceremonia", f"{s.codigo} · {s.fallecido}", reverse("solicitud_detail", args=[s.pk]))
    if tipo in ("todo", "velatorio") and not solo_chofer:
        for s in Solicitud.objects.filter(sala__isnull=False, velatorio_inicio__lt=fin, velatorio_fin__gt=ini).select_related("sala"):
            a, b = timezone.localtime(s.velatorio_inicio), timezone.localtime(s.velatorio_fin)
            dia = a.date()
            while dia <= b.date():
                hora = a.strftime("%H:%M") if dia == a.date() else "Todo el día"
                poner(dia, hora, "velatorio", "Velatorio", f"{s.sala.nombre} · {s.codigo} · {s.fallecido}", reverse("solicitud_detail", args=[s.pk]))
                dia += timedelta(days=1)
    if tipo in ("todo", "ausencia") and not solo_chofer:
        for a in Ausencia.objects.filter(estado=Ausencia.Estado.APROBADA, desde__lt=fin.date(), hasta__gte=ini.date()).select_related("usuario"):
            dia = max(a.desde, ini.date())
            while dia <= min(a.hasta, fin.date()):
                poner(dia, "Todo el día", "ausencia", a.get_tipo_display(), a.usuario.get_full_name() or a.usuario.username,
                      reverse("ausencia_list") if request.user.is_superuser else reverse("calendario"))
                dia += timedelta(days=1)
    if tipo in ("todo", "traslado"):
        qs = Traslado.objects.filter(inicio__gte=ini, inicio__lt=fin).select_related("vehiculo", "chofer", "solicitud")
        if solo_chofer:
            qs = qs.filter(chofer=request.user)
        for tr in qs:
            f = timezone.localtime(tr.inicio)
            quien = "" if solo_chofer else f" · {tr.chofer.get_full_name() or tr.chofer.username}"
            poner(f.date(), f.strftime("%H:%M"), "traslado", "Traslado", f"{tr.codigo} · {tr.destino}{quien}",
                  reverse("traslado_list") if solo_chofer else reverse("traslado_edit", args=[tr.pk]))
    hoy = timezone.localdate()
    grilla = [[{"fecha": d, "en_mes": d.month == primero.month, "hoy": d == hoy,
                "eventos": sorted(eventos[d], key=lambda e: (e["hora"] != "Todo el día", e["hora"]))} for d in sem] for sem in semanas]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return render(request, "servicios/calendario.html", {
        "grilla": grilla, "titulo_mes": f"{meses[primero.month - 1].capitalize()} {primero.year}",
        "prev": _mover(primero, -1).strftime("%Y-%m"), "sig": _mover(primero, 1).strftime("%Y-%m"),
        "mes_actual": hoy.strftime("%Y-%m"), "mes": primero.strftime("%Y-%m"), "tipo": tipo, "tipos": TIPOS_CALENDARIO, "solo_chofer": solo_chofer,
        "total": sum(len(v) for v in eventos.values()),
        "dias": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]})


# ==========================================================================
# Precios
# ==========================================================================
@rol_requerido(RECEPCION, BODEGA)
def precios(request):
    return render(request, "servicios/precios.html", {
        "feretros": Articulo.objects.filter(categoria="feretros").order_by("precio"),
        "otros": Articulo.objects.exclude(categoria="feretros").filter(precio__gt=0).order_by("categoria", "nombre"),
        "tarifas": Tarifa.objects.order_by("precio"),
        "puede_editar_tarifas": request.user.is_superuser})


@rol_requerido()
def tarifas_editar(request):
    for tipo, _ in Solicitud.Servicio.choices:
        Tarifa.objects.get_or_create(tipo_servicio=tipo)
    formset = forms.TarifaFormSet(request.POST or None, queryset=Tarifa.objects.order_by("tipo_servicio"))
    if request.method == "POST" and formset.is_valid():
        formset.save()
        services.registrar(request.user, "Tarifas de servicios actualizadas", "")
        messages.success(request, "Las tarifas se guardaron.")
        return redirect("precios")
    return render(request, "servicios/tarifas_form.html", {"formset": formset})


# ==========================================================================
# Ventas: boleta / factura
# ==========================================================================
class VentaLista(RolesMixin, ListView):
    roles_permitidos = {RECEPCION}
    template_name = "servicios/venta_list.html"
    paginate_by = 15

    def get_queryset(self):
        qs = Venta.objects.select_related("solicitud", "creada_por")
        tipo = self.request.GET.get("tipo", "")
        if tipo in dict(Venta.Tipo.choices):
            qs = qs.filter(tipo_documento=tipo)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(numero__icontains=q) | Q(cliente_nombre__icontains=q) | Q(cliente_rut__icontains=q) | Q(solicitud__codigo__icontains=q) | Q(solicitud__fallecido__icontains=q))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(q=self.request.GET.get("q", ""), tipo=self.request.GET.get("tipo", ""), tipos=Venta.Tipo.choices)
        return ctx


@rol_requerido(RECEPCION)
def venta_crear(request, pk):
    s = get_object_or_404(Solicitud.objects.select_related("feretro"), pk=pk)
    iniciales = []
    tf = services.tarifas()
    if tf.get(s.tipo_servicio):
        iniciales.append({"item": f"s:{s.tipo_servicio}", "cantidad": 1})
    if s.feretro_id and s.feretro.precio:
        iniciales.append({"item": f"a:{s.feretro_id}", "cantidad": 1})
    form = forms.VentaForm(request.POST or None, initial={"cliente_nombre": s.contacto_nombre})
    formset = forms.VentaLineasFormSet(request.POST or None, initial=iniciales if request.method != "POST" else None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lineas = [f.resolver() for f in formset if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        try:
            datos = {k: form.cleaned_data[k] for k in form.fields}
            venta = services.crear_venta(s, request.user, datos, lineas)
        except ValidationError as e:
            avisar_errores(request, e)
        else:
            messages.success(request, f"{venta.get_tipo_documento_display()} {venta.numero} emitida por {forms.clp(venta.total)}.")
            return redirect("venta_detail", pk=venta.pk)
    return render(request, "servicios/venta_form.html", {"form": form, "formset": formset, "s": s})


@rol_requerido(RECEPCION)
def venta_detalle(request, pk):
    venta = get_object_or_404(Venta.objects.select_related("solicitud", "creada_por").prefetch_related("items"), pk=pk)
    return render(request, "servicios/venta_detail.html", {"v": venta, "empresa": settings.EMPRESA})


@rol_requerido(RECEPCION)
def venta_pdf(request, pk):
    from .documentos_pdf import pdf_venta
    venta = get_object_or_404(Venta.objects.select_related("solicitud").prefetch_related("items"), pk=pk)
    resp = HttpResponse(pdf_venta(venta), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{venta.get_tipo_documento_display().lower()}_{venta.numero}.pdf"'
    return resp


@require_POST
@rol_requerido()
def venta_anular(request, pk):
    venta = get_object_or_404(Venta, pk=pk)
    try:
        services.anular_venta(venta, request.user)
        messages.success(request, f"{venta} anulada.")
    except ValidationError as e:
        avisar_errores(request, e)
    return redirect("venta_detail", pk=pk)


# ==========================================================================
# Ausencias del personal
# ==========================================================================
def _errores_a_formulario(form, error):
    if hasattr(error, "error_dict"):
        for campo, errs in error.message_dict.items():
            for e in errs:
                form.add_error(campo if campo in form.fields else None, e)
    else:
        for e in error.messages:
            form.add_error(None, e)


@rol_requerido(RECEPCION, BODEGA, CHOFER)
def ausencia_mias(request):
    """Cada persona ve y pide sus ausencias."""
    form = forms.AusenciaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            aus = services.solicitar_ausencia(request.user, form.cleaned_data)
        except ValidationError as e:
            _errores_a_formulario(form, e)
        else:
            messages.success(request, f"Solicitud enviada: {aus.get_tipo_display().lower()} del {aus.desde:%d/%m} al {aus.hasta:%d/%m}. El administrador la revisará.")
            return redirect("ausencia_mias")
    return render(request, "servicios/ausencia_mias.html", {
        "form": form, "mias": Ausencia.objects.filter(usuario=request.user).order_by("-desde")[:30], "hoy": timezone.localdate()})


class AusenciaLista(RolesMixin, ListView):
    roles_permitidos = set()  # solo administrador
    template_name = "servicios/ausencia_list.html"
    paginate_by = 20

    def get_queryset(self):
        qs = Ausencia.objects.select_related("usuario", "resuelta_por")
        estado = self.request.GET.get("estado", "")
        if estado in dict(Ausencia.Estado.choices):
            qs = qs.filter(estado=estado)
        if self.request.GET.get("persona", "").isdigit():
            qs = qs.filter(usuario_id=int(self.request.GET["persona"]))
        return qs.order_by("-desde")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        hoy = timezone.localdate()
        ctx.update(
            pendientes=Ausencia.objects.filter(estado=Ausencia.Estado.PENDIENTE).select_related("usuario").order_by("desde"),
            hoy=hoy, ausentes_hoy=services.ausentes_en(hoy),
            estados=Ausencia.Estado.choices, estado_sel=self.request.GET.get("estado", ""),
            persona_sel=self.request.GET.get("persona", ""),
            personas=User.objects.filter(ausencias__isnull=False).distinct().order_by("first_name", "username"))
        return ctx


@rol_requerido()
def ausencia_crear(request):
    """El administrador registra una ausencia de cualquier persona (queda aprobada)."""
    form = forms.AusenciaForm(request.POST or None, admin=True)
    if request.method == "POST" and form.is_valid():
        datos = dict(form.cleaned_data)
        persona = datos.pop("usuario")
        try:
            services.solicitar_ausencia(persona, datos, por=request.user)
        except ValidationError as e:
            _errores_a_formulario(form, e)
        else:
            messages.success(request, "Ausencia registrada.")
            return redirect("ausencia_list")
    return render(request, "servicios/form.html", {"form": form, "titulo": "Registrar ausencia", "cancelar_url": reverse("ausencia_list")})


@require_POST
@rol_requerido()
def ausencia_resolver(request, pk):
    a = get_object_or_404(Ausencia, pk=pk)
    try:
        a = services.resolver_ausencia(a, request.POST.get("accion", ""), request.user, request.POST.get("comentario", ""))
        messages.success(request, f"Ausencia de {a.usuario.get_full_name() or a.usuario.username}: {a.get_estado_display().lower()}.")
    except ValidationError as e:
        avisar_errores(request, e)
    return redirect(request.POST.get("volver") if (request.POST.get("volver") or "").startswith("/") else "ausencia_list")


@require_POST
@rol_requerido(RECEPCION, BODEGA, CHOFER)
def ausencia_cancelar(request, pk):
    a = get_object_or_404(Ausencia, pk=pk)
    try:
        services.cancelar_ausencia(a, request.user)
        messages.success(request, "La solicitud se retiró.")
    except ValidationError as e:
        avisar_errores(request, e)
    return redirect("ausencia_list" if request.user.is_superuser else "ausencia_mias")


# ==========================================================================
# Errores
# ==========================================================================
def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)



# ==========================================================================
# Urnas y féretros: eliminar y galería de fotos (Bodega)
# ==========================================================================
class ArticuloEliminar(RolesMixin, DeleteView):
    roles_permitidos = {BODEGA}
    model = Articulo
    template_name = "servicios/articulo_confirm_delete.html"
    success_url = reverse_lazy("articulo_list")

    def form_valid(self, form):
        nombre = str(self.object)
        try:
            resp = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, "No se puede eliminar: el artículo tiene movimientos, pedidos o ventas registrados. "
                                         "Para dejar de ofrecerlo, desmarca «mostrar en el sitio público».")
            return redirect("articulo_edit", pk=self.object.pk)
        services.registrar(self.request.user, "Artículo eliminado", nombre)
        messages.success(self.request, f"«{nombre}» fue eliminado.")
        return resp


@rol_requerido(BODEGA)
def articulo_fotos(request, pk):
    art = get_object_or_404(Articulo.objects.prefetch_related("fotos"), pk=pk)
    form = forms.ArticuloFotoForm(request.POST or None, request.FILES or None, articulo=art)
    if request.method == "POST" and form.is_valid():
        foto = form.save(commit=False)
        foto.articulo = art
        foto.save()
        services.registrar(request.user, "Foto agregada", art.nombre)
        messages.success(request, "La foto se agregó.")
        return redirect("articulo_fotos", pk=art.pk)
    return render(request, "servicios/articulo_fotos.html", {"articulo": art, "form": form})


@require_POST
@rol_requerido(BODEGA)
def articulo_foto_eliminar(request, pk, foto_pk):
    foto = get_object_or_404(ArticuloFoto, pk=foto_pk, articulo_id=pk)
    foto.imagen.delete(save=False)
    foto.delete()
    services.registrar(request.user, "Foto eliminada", foto.articulo)
    messages.success(request, "La foto se eliminó.")
    return redirect("articulo_fotos", pk=pk)


# ==========================================================================
# Etiquetas de solicitudes
# ==========================================================================
class EtiquetaLista(RolesMixin, ListView):
    roles_permitidos = {RECEPCION}
    template_name = "servicios/etiqueta_list.html"

    def get_queryset(self):
        from django.db.models import Count
        return Etiqueta.objects.annotate(usos=Count("solicitudes")).order_by("nombre")


class EtiquetaCrear(RolesMixin, FormularioMixin, CreateView):
    roles_permitidos = {RECEPCION}
    model = Etiqueta
    form_class = forms.EtiquetaForm
    titulo = "Nueva etiqueta"
    volver = reverse_lazy("etiqueta_list")
    success_url = reverse_lazy("etiqueta_list")
    success_message = "Etiqueta creada."


class EtiquetaEditar(RolesMixin, FormularioMixin, UpdateView):
    roles_permitidos = {RECEPCION}
    model = Etiqueta
    form_class = forms.EtiquetaForm
    titulo = "Editar etiqueta"
    volver = reverse_lazy("etiqueta_list")
    success_url = reverse_lazy("etiqueta_list")
    success_message = "Los cambios se guardaron."


@require_POST
@rol_requerido(RECEPCION)
def etiqueta_eliminar(request, pk):
    e = get_object_or_404(Etiqueta, pk=pk)
    nombre = e.nombre
    e.delete()
    services.registrar(request.user, "Etiqueta eliminada", nombre)
    messages.success(request, f"La etiqueta «{nombre}» se eliminó de todas las solicitudes.")
    return redirect("etiqueta_list")


# ==========================================================================
# Perfiles del personal
# ==========================================================================
def _perfil_de(usuario):
    return Perfil.objects.get_or_create(usuario=usuario)[0]


@login_required
def mi_perfil(request):
    perfil = _perfil_de(request.user)
    form = forms.PerfilForm(request.POST or None, instance=perfil, usuario=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.registrar(request.user, "Perfil actualizado", request.user.username)
        messages.success(request, "Tu perfil se actualizó.")
        return redirect("perfil")
    return render(request, "servicios/perfil.html", {"form": form, "persona": request.user, "propio": True})


@rol_requerido()
def usuario_editar(request, pk):
    persona = get_object_or_404(User, pk=pk)
    perfil = _perfil_de(persona)
    form = forms.PerfilForm(request.POST or None, instance=perfil, usuario=persona)
    rol_form = forms.RolForm(request.POST or None, initial={"rol": rol_principal(persona)})
    if request.method == "POST" and form.is_valid() and rol_form.is_valid():
        try:
            services.cambiar_rol(persona, rol_form.cleaned_data["rol"], request.user)
        except ValidationError as e:
            avisar_errores(request, e)
        else:
            form.save()
            services.registrar(request.user, "Perfil de usuario actualizado", persona.username)
            messages.success(request, f"Se guardaron los datos de {persona.get_full_name() or persona.username}.")
            return redirect("usuario_list")
    return render(request, "servicios/perfil.html", {"form": form, "rol_form": rol_form, "persona": persona, "propio": False})


# ==========================================================================
# Abonos y pagos con Webpay
# ==========================================================================
@require_POST
@rol_requerido(RECEPCION)
def abono_crear(request, pk):
    s = get_object_or_404(Solicitud, pk=pk)
    form = forms.AbonoForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.registrar_abono(s, request.user, d["monto"], d["medio"], d["referencia"])
            messages.success(request, "El abono quedó registrado.")
        except ValidationError as e:
            avisar_errores(request, e)
    else:
        for errores in form.errors.values():
            for e in errores:
                messages.error(request, e)
    return redirect("solicitud_detail", pk=s.pk)


@require_POST
@rol_requerido()
def abono_anular(request, pk):
    ab = get_object_or_404(Abono, pk=pk)
    try:
        services.anular_abono(ab, request.user)
        messages.success(request, "El abono fue anulado.")
    except ValidationError as e:
        avisar_errores(request, e)
    return redirect("solicitud_detail", pk=ab.solicitud_id)


@require_POST
@rol_requerido(RECEPCION)
def webpay_iniciar(request, pk):
    s = get_object_or_404(Solicitud, pk=pk)
    form = forms.MontoWebpayForm(request.POST)
    if not form.is_valid():
        for errores in form.errors.values():
            for e in errores:
                messages.error(request, e)
        return redirect("solicitud_detail", pk=s.pk)
    try:
        url, token = services.iniciar_webpay(s, request.user, form.cleaned_data["monto"],
                                             request.build_absolute_uri(reverse("webpay_retorno")))
    except ValidationError as e:
        avisar_errores(request, e)
        return redirect("solicitud_detail", pk=s.pk)
    return render(request, "servicios/webpay_redirigir.html", {"url": url, "token": token, "solicitud": s})


@csrf_exempt
def webpay_retorno(request):
    """
    Webpay devuelve aquí al cliente (por GET o POST, desde otro sitio: no lleva el token CSRF ni, a veces, la sesión).
    No exige sesión: solo actúa sobre un token de pago pendiente (imposible de adivinar) y luego manda a la pantalla del resultado.
    """
    datos = request.POST if request.method == "POST" else request.GET
    token = datos.get("token_ws") or datos.get("TBK_TOKEN") or datos.get("TBK_ORDEN_COMPRA") or ""
    cancelado = not datos.get("token_ws")
    ab = services.cerrar_webpay(token, cancelado=cancelado) if token else None
    if ab is None:
        return redirect("home")
    return redirect("pago_resultado", pk=ab.pk)


@login_required
def pago_resultado(request, pk):
    if not tiene_rol(request.user, {RECEPCION}):
        raise PermissionDenied
    ab = get_object_or_404(Abono.objects.select_related("solicitud"), pk=pk)
    return render(request, "servicios/pago_resultado.html", {"abono": ab})


from django.contrib.auth.views import PasswordChangeView  # noqa: E402


class CambiarClave(SuccessMessageMixin, PasswordChangeView):
    template_name = "servicios/clave_form.html"
    success_url = reverse_lazy("perfil")
    success_message = "Tu contraseña se cambió."
