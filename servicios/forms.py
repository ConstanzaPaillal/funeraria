"""Formularios de Cristo Rey (validan en el servidor, sin depender del JavaScript)."""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, User
from django.forms import formset_factory, inlineformset_factory, modelformset_factory
from django.utils import timezone

from .models import MAX_FOTOS_EXTRA, Abono, Articulo, ArticuloFoto, Ausencia, Etiqueta, Movimiento, Perfil, Pedido, PedidoItem, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta, validar_rut
from .permissions import ADMIN, BODEGA, CHOFER, ETIQUETAS, GRUPO_DE_ROL, RECEPCION, tiene_rol

FORMATO_FH = "%Y-%m-%dT%H:%M"
OPCION_VACIA = "Elige una opción"


class VaciosEnEspanol:
    """Django escribe «---------» o «Select an option» en los selects en blanco: aquí queda en español."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo, forms.ModelChoiceField):
                if campo.empty_label and campo.empty_label.strip("- ") in ("", "Select an option"):
                    campo.empty_label = OPCION_VACIA
            elif isinstance(campo, forms.ChoiceField) and campo.choices and campo.choices[0][0] == "":
                if str(campo.choices[0][1]).strip("- ") in ("", "Select an option"):
                    campo.choices = [("", OPCION_VACIA)] + list(campo.choices)[1:]


class BaseModelForm(VaciosEnEspanol, forms.ModelForm):
    pass


class BaseForm(VaciosEnEspanol, forms.Form):
    pass


class FechaHoraMixin:
    """Los campos de fecha y hora usan el selector nativo del navegador (datetime-local)."""
    def _fechas(self):
        for campo in self.fields.values():
            if isinstance(campo, forms.DateTimeField):
                campo.widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format=FORMATO_FH)
                campo.input_formats = [FORMATO_FH, "%Y-%m-%d %H:%M"]


def usuarios_con_rol(rol):
    return User.objects.filter(is_active=True, groups__name=GRUPO_DE_ROL[rol]).order_by("first_name", "last_name", "username")


class NombreUsuarioChoice(forms.ModelChoiceField):
    def label_from_instance(self, u):
        return u.get_full_name() or u.username


def clp(n):
    return "$" + f"{int(n):,}".replace(",", ".")


class FeretroChoice(forms.ModelChoiceField):
    def label_from_instance(self, a):
        return f"{a.nombre} · {clp(a.precio)}" if a.precio else a.nombre


class SolicitudForm(FechaHoraMixin, BaseModelForm):
    class Meta:
        model = Solicitud
        fields = ["fallecido", "contacto_nombre", "contacto_telefono", "contacto_email", "tipo_servicio",
                  "ceremonia_inicio", "lugar_ceremonia", "feretro", "etiquetas", "sala", "velatorio_inicio", "velatorio_fin", "responsable"]
        widgets = {"etiquetas": forms.CheckboxSelectMultiple}
        help_texts = {
            "contacto_telefono": "Ejemplo: +56 9 1234 5678. Los últimos 4 dígitos sirven de verificación a la familia.",
            "contacto_email": "Opcional. Se usa para avisar si faltan documentos.",
            "sala": "Opcional. Si reservas sala, indica también el horario del velatorio.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self._fechas()
        self.fields["sala"].queryset = Sala.objects.filter(activa=True)
        self.fields["feretro"] = FeretroChoice(queryset=Articulo.objects.filter(categoria="feretros").order_by("precio"),
                                               required=False, label="Féretro elegido", empty_label="Aún sin elegir")
        self.fields["responsable"] = NombreUsuarioChoice(queryset=usuarios_con_rol(RECEPCION), required=False, label="Responsable")
        if user is not None and not tiene_rol(user, {ADMIN}):
            del self.fields["responsable"]  # recepción queda como responsable de lo que crea

    def clean_ceremonia_inicio(self):
        f = self.cleaned_data["ceremonia_inicio"]
        if not self.instance.pk and f < timezone.now():
            raise forms.ValidationError("La ceremonia debe ser en una fecha y hora futuras.")
        return f

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.pk and "responsable" not in self.fields and self.user is not None:
            obj.responsable = self.user
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class TrasladoForm(FechaHoraMixin, BaseModelForm):
    class Meta:
        model = Traslado
        fields = ["solicitud", "origen", "destino", "inicio", "duracion_min", "vehiculo", "chofer"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fechas()
        self.fields["solicitud"].queryset = Solicitud.objects.exclude(etapa=Solicitud.Etapa.FINALIZADO)
        self.fields["chofer"] = NombreUsuarioChoice(queryset=usuarios_con_rol(CHOFER), label="Chofer")
        self.fields["vehiculo"].queryset = Vehiculo.objects.exclude(estado=Vehiculo.Estado.FUERA)


class VehiculoForm(BaseModelForm):
    class Meta:
        model = Vehiculo
        fields = ["nombre", "patente", "tipo", "estado"]


class ArticuloForm(BaseModelForm):
    class Meta:
        model = Articulo
        fields = ["nombre", "categoria", "gama", "precio", "descripcion", "publicado", "imagen",
                  "largo_cm", "ancho_cm", "alto_cm", "material", "unidad", "stock", "stock_minimo"]
        help_texts = {"stock": "Stock inicial. Después solo cambia con entradas y salidas registradas."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["stock"]

    def clean(self):
        datos = super().clean()
        if datos.get("categoria") == "feretros" and not datos.get("gama"):
            self.add_error("gama", "Indica si el féretro es básico, clásico o premium.")
        if datos.get("categoria") != "feretros":
            datos["gama"] = ""
        return datos


class MovimientoForm(BaseForm):
    articulo = forms.ModelChoiceField(Articulo.objects.all(), label="Artículo")
    tipo = forms.ChoiceField(choices=Movimiento.Tipo.choices, label="Tipo de movimiento")
    cantidad = forms.IntegerField(min_value=1, label="Cantidad")
    motivo = forms.CharField(max_length=150, required=False, label="Motivo")

    def clean(self):
        datos = super().clean()
        art, tipo, cant = datos.get("articulo"), datos.get("tipo"), datos.get("cantidad")
        if art and tipo == Movimiento.Tipo.SALIDA and cant and cant > art.stock:
            self.add_error("cantidad", f"No puedes sacar {cant}: en bodega solo hay {art.stock} de «{art.nombre}».")
        return datos


class PedidoForm(BaseModelForm):
    class Meta:
        model = Pedido
        fields = ["solicitud"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["solicitud"].queryset = Solicitud.objects.exclude(etapa=Solicitud.Etapa.FINALIZADO)


PedidoItemFormSet = inlineformset_factory(
    Pedido, PedidoItem, fields=("articulo", "cantidad"), extra=2, min_num=1, validate_min=True, can_delete=True,
    labels={"articulo": "Material", "cantidad": "Cantidad"},
)


class UsuarioForm(UserCreationForm):
    ROLES = [(RECEPCION, ETIQUETAS[RECEPCION]), (BODEGA, ETIQUETAS[BODEGA]), (CHOFER, ETIQUETAS[CHOFER]), (ADMIN, ETIQUETAS[ADMIN])]
    rol = forms.ChoiceField(choices=ROLES, label="Rol")

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")
        labels = {"username": "Nombre de usuario", "first_name": "Nombre", "last_name": "Apellido", "email": "Correo"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("first_name", "last_name"):
            self.fields[f].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        rol = self.cleaned_data["rol"]
        if rol == ADMIN:
            user.is_staff = user.is_superuser = True
        if commit:
            user.save()
            if rol != ADMIN:
                user.groups.add(Group.objects.get(name=GRUPO_DE_ROL[rol]))
        return user


class FamiliaForm(BaseForm):
    codigo = forms.CharField(max_length=10, label="Código de la solicitud", help_text="Por ejemplo: CR-0142")
    verificacion = forms.CharField(max_length=20, label="Últimos 4 dígitos del teléfono de contacto",
                                   help_text="El teléfono que dejó la familia al solicitar el servicio.")


# --------------------------------------------------------------------------
# Ventas
# --------------------------------------------------------------------------
class VentaForm(BaseModelForm):
    class Meta:
        model = Venta
        fields = ["tipo_documento", "cliente_nombre", "cliente_rut", "cliente_giro", "cliente_direccion", "forma_pago"]
        labels = {"tipo_documento": "Documento a emitir", "cliente_nombre": "Nombre o razón social"}
        help_texts = {"cliente_rut": "Obligatorio en factura. En boleta es opcional.",
                      "cliente_giro": "Solo factura.", "cliente_direccion": "Solo factura."}


def opciones_venta():
    """Catálogo vendible: servicios con tarifa y artículos con precio. Valores: 's:<tipo>' o 'a:<id>'."""
    servicios = [(f"s:{t.tipo_servicio}", f"{t.get_tipo_servicio_display()} · {clp(t.precio)}") for t in Tarifa.objects.filter(precio__gt=0).order_by("precio")]
    arts = Articulo.objects.filter(precio__gt=0).order_by("categoria", "precio")
    feretros = [(f"a:{a.pk}", f"{a.nombre} · {clp(a.precio)}") for a in arts if a.categoria == "feretros"]
    otros = [(f"a:{a.pk}", f"{a.nombre} · {clp(a.precio)}") for a in arts if a.categoria != "feretros"]
    grupos = [("", "Elige un producto o servicio")]
    for nombre, lista in (("Servicios", servicios), ("Féretros", feretros), ("Otros artículos", otros)):
        if lista:
            grupos.append((nombre, lista))
    return grupos


class LineaVentaForm(BaseForm):
    item = forms.ChoiceField(label="Producto o servicio", choices=[])
    cantidad = forms.IntegerField(label="Cantidad", min_value=1, max_value=99, initial=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].choices = opciones_venta()

    def resolver(self):
        """Devuelve (descripcion, cantidad, precio_unitario, articulo|None) con el precio vigente del catálogo."""
        valor, cant = self.cleaned_data["item"], self.cleaned_data["cantidad"]
        tipo, clave = valor.split(":", 1)
        if tipo == "s":
            t = Tarifa.objects.get(tipo_servicio=clave)
            return (t.get_tipo_servicio_display(), cant, t.precio, None)
        a = Articulo.objects.get(pk=int(clave))
        return (a.nombre, cant, a.precio, a)


VentaLineasFormSet = formset_factory(LineaVentaForm, extra=2, min_num=1, validate_min=True, can_delete=True)

TarifaFormSet = modelformset_factory(Tarifa, fields=("precio", "duracion_horas", "descripcion", "activo"), extra=0)


class ArticuloFotoForm(BaseModelForm):
    class Meta:
        model = ArticuloFoto
        fields = ["imagen", "descripcion"]

    def __init__(self, *args, articulo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.articulo = articulo
        self.fields["imagen"].required = True

    def clean(self):
        datos = super().clean()
        if self.articulo and self.articulo.fotos.count() >= MAX_FOTOS_EXTRA:
            raise forms.ValidationError(f"Cada artículo admite hasta {MAX_FOTOS_EXTRA} fotos adicionales. Elimina una para subir otra.")
        return datos


class EtiquetaForm(BaseModelForm):
    class Meta:
        model = Etiqueta
        fields = ["nombre", "color"]


class AbonoForm(BaseForm):
    monto = forms.IntegerField(min_value=1000, label="Monto (CLP)", help_text="Mínimo $1.000. No puede superar el saldo pendiente.")
    medio = forms.ChoiceField(choices=[(v, l) for v, l in Abono.Medio.choices if v != Abono.Medio.WEBPAY], label="Medio de pago")
    referencia = forms.CharField(max_length=80, required=False, label="Referencia o comprobante",
                                 help_text="Opcional: número de transferencia, voucher, etc.")


class MontoWebpayForm(BaseForm):
    monto = forms.IntegerField(min_value=1000, label="Monto a pagar con Webpay (CLP)")


class PerfilForm(BaseModelForm):
    """Datos de contacto del personal. Nombre y correo viven en el usuario."""
    first_name = forms.CharField(max_length=150, label="Nombre")
    last_name = forms.CharField(max_length=150, label="Apellido")
    email = forms.EmailField(required=False, label="Correo")

    class Meta:
        model = Perfil
        fields = ["rut", "telefono", "cargo", "direccion", "contacto_emergencia"]

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.order_fields(["first_name", "last_name", "email", "rut", "telefono", "cargo", "direccion", "contacto_emergencia"])
        if usuario:
            self.fields["first_name"].initial, self.fields["last_name"].initial, self.fields["email"].initial = usuario.first_name, usuario.last_name, usuario.email

    def save(self, commit=True):
        p = super().save(commit=False)
        p.usuario = self.usuario
        u = self.usuario
        u.first_name, u.last_name, u.email = self.cleaned_data["first_name"], self.cleaned_data["last_name"], self.cleaned_data["email"]
        if commit:
            u.save(update_fields=["first_name", "last_name", "email"])
            p.save()
        return p


class RolForm(BaseForm):
    """Solo el administrador cambia el rol de otra persona."""
    rol = forms.ChoiceField(choices=UsuarioForm.ROLES, label="Rol")


# --------------------------------------------------------------------------
# Ausencias
# --------------------------------------------------------------------------
class AusenciaForm(BaseModelForm):
    """Cada persona pide la suya. El administrador puede elegir de quién es (y queda aprobada al guardar)."""
    class Meta:
        model = Ausencia
        fields = ["tipo", "desde", "hasta", "motivo"]
        widgets = {"desde": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "hasta": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        labels = {"desde": "Desde (primer día)", "hasta": "Hasta (último día)"}
        help_texts = {"motivo": "Opcional. Ayuda al administrador a decidir."}

    def __init__(self, *args, admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.admin = admin
        if admin:
            self.fields["usuario"] = NombreUsuarioChoice(queryset=User.objects.filter(is_active=True).order_by("first_name", "username"),
                                                         label="Persona")
            self.order_fields(["usuario", "tipo", "desde", "hasta", "motivo"])

    def clean(self):
        d = super().clean()
        if d.get("desde") and d.get("hasta") and d["hasta"] < d["desde"]:
            self.add_error("hasta", "La fecha de término no puede ser anterior al inicio.")
        return d
