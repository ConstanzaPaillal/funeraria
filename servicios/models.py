"""
Modelos de Cristo Rey.

Relaciones principales
  Solicitud 1─N Documento · 1─N Traslado · 1─N Pedido ─ 1─N PedidoItem ─ N─1 Articulo
  Solicitud N─1 Sala (velatorio) · N─1 User (responsable de recepción)
  Traslado  N─1 Vehiculo · N─1 User (chofer)
  Articulo  1─N Movimiento (entradas y salidas de bodega)
  Actividad: registro de quién hizo qué y cuándo.

Las reglas que cruzan varias tablas (solapamientos, stock, avance de etapas) viven en
services.py; aquí quedan las validaciones propias de cada modelo.
"""
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone


def validar_telefono(valor):
    if len(re.findall(r"\d", valor or "")) < 8:
        raise ValidationError("Escribe un teléfono con al menos 8 dígitos.")


def ruta_imagen(instancia, nombre):
    """Las fotos se guardan con un nombre aleatorio: evita choques y nombres con caracteres raros."""
    return f"catalogo/{uuid.uuid4().hex}.{nombre.rsplit('.', 1)[-1].lower()}"


def validar_peso_imagen(archivo):
    if archivo.size > 3 * 1024 * 1024:
        raise ValidationError("La imagen pesa más de 3 MB. Reduce su tamaño e inténtalo de nuevo.")


def normalizar_rut(valor):
    """'12.345.678-5' -> '12345678-5' (mayúscula en el dígito verificador)."""
    limpio = re.sub(r"[^0-9kK]", "", valor or "").upper()
    return f"{limpio[:-1]}-{limpio[-1]}" if len(limpio) >= 2 else limpio


def validar_rut(valor):
    rut = normalizar_rut(valor)
    if not re.fullmatch(r"\d{6,8}-[0-9K]", rut):
        raise ValidationError("Escribe un RUT válido, por ejemplo 12.345.678-5.")
    cuerpo, dv = rut.split("-")
    suma = sum(int(d) * f for d, f in zip(reversed(cuerpo), [2, 3, 4, 5, 6, 7] * 2))
    esperado = 11 - suma % 11
    esperado = {11: "0", 10: "K"}.get(esperado, str(esperado))
    if dv != esperado:
        raise ValidationError("El RUT no es válido: revisa el dígito verificador.")


# ==========================================================================
# Infraestructura: salas y vehículos
# ==========================================================================
class Sala(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    capacidad = models.PositiveSmallIntegerField(default=40, validators=[MinValueValidator(1)])
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Vehiculo(models.Model):
    class Tipo(models.TextChoices):
        CARROZA = "carroza", "Carroza"
        FURGON = "furgon", "Furgón de traslado"
        APOYO = "apoyo", "Vehículo de apoyo"

    class Estado(models.TextChoices):
        DISPONIBLE = "disponible", "Disponible"
        EN_RUTA = "en_ruta", "En ruta"
        MANTENCION = "mantencion", "En mantención"
        FUERA = "fuera_servicio", "Fuera de servicio"

    nombre = models.CharField(max_length=60)
    patente = models.CharField(max_length=10, unique=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.CARROZA)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.DISPONIBLE)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.patente})"

    @property
    def utilizable(self):
        return self.estado in (self.Estado.DISPONIBLE, self.Estado.EN_RUTA)


# ==========================================================================
# Solicitudes de servicio
# ==========================================================================
class Etiqueta(models.Model):
    """Etiqueta para clasificar solicitudes (ej.: «Convenio», «Prioridad»). El color siempre va con el nombre."""
    class Color(models.TextChoices):
        VERDE = "ok", "Verde"
        AMBAR = "warn", "Ámbar"
        ROJO = "crit", "Rojo"
        GRIS = "neutral", "Gris"

    nombre = models.CharField(max_length=40, unique=True)
    color = models.CharField(max_length=8, choices=Color.choices, default=Color.GRIS)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Solicitud(models.Model):
    class Servicio(models.TextChoices):
        VELATORIO_SEPULTACION = "velatorio_sepultacion", "Velatorio y sepultación"
        CREMACION = "cremacion", "Cremación"
        TRASLADO_CREMACION = "traslado_cremacion", "Traslado y cremación"
        VELATORIO_CREMACION = "velatorio_cremacion", "Velatorio y cremación"

    class Etapa(models.IntegerChoices):
        RECIBIDA = 0, "Solicitud recibida"
        PREPARACION = 1, "Preparación"
        VELATORIO = 2, "Velatorio"
        TRASLADO = 3, "Traslado"
        CEREMONIA = 4, "Ceremonia"
        FINALIZADO = 5, "Servicio finalizado"

    codigo = models.CharField(max_length=10, unique=True, editable=False, blank=True)
    fallecido = models.CharField("nombre del fallecido/a", max_length=150)
    contacto_nombre = models.CharField("familiar de contacto", max_length=120)
    contacto_telefono = models.CharField("teléfono de contacto", max_length=30, validators=[validar_telefono])
    contacto_email = models.EmailField("correo de contacto", blank=True)
    tipo_servicio = models.CharField("tipo de servicio", max_length=25, choices=Servicio.choices)
    ceremonia_inicio = models.DateTimeField("fecha y hora de la ceremonia")
    lugar_ceremonia = models.CharField("lugar de la ceremonia", max_length=150)
    sala = models.ForeignKey(Sala, null=True, blank=True, on_delete=models.PROTECT, related_name="solicitudes",
                             verbose_name="sala de velatorio")
    velatorio_inicio = models.DateTimeField("inicio del velatorio", null=True, blank=True)
    velatorio_fin = models.DateTimeField("término del velatorio", null=True, blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="solicitudes_a_cargo")
    feretro = models.ForeignKey("Articulo", null=True, blank=True, on_delete=models.SET_NULL, related_name="solicitudes",
                                limit_choices_to={"categoria": "feretros"}, verbose_name="féretro elegido")
    etiquetas = models.ManyToManyField(Etiqueta, blank=True, related_name="solicitudes")
    etapa = models.PositiveSmallIntegerField(choices=Etapa.choices, default=Etapa.RECIBIDA)
    aviso_enviado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ceremonia_inicio"]
        verbose_name = "solicitud de servicio"
        verbose_name_plural = "solicitudes de servicio"
        indexes = [models.Index(fields=["sala", "velatorio_inicio"])]

    def __str__(self):
        return f"{self.codigo} · {self.fallecido}"

    # --- estado calculado ---------------------------------------------------
    @property
    def finalizada(self):
        return self.etapa >= self.Etapa.FINALIZADO

    @property
    def documentos_pendientes(self):
        return [d for d in self.documentos.all() if not d.entregado]

    @property
    def estado(self):
        """finalizada | en_curso | docs_pendientes | urgente"""
        if self.finalizada:
            return "finalizada"
        if not self.documentos_pendientes:
            return "en_curso"
        horas = (self.ceremonia_inicio - timezone.now()).total_seconds() / 3600
        return "urgente" if horas < settings.HORAS_URGENTE else "docs_pendientes"

    @property
    def etapa_texto(self):
        return self.Etapa(self.etapa).label

    @property
    def verificacion_familia(self):
        """Últimos 4 dígitos del teléfono de contacto (segundo dato para la vista pública)."""
        return "".join(re.findall(r"\d", self.contacto_telefono))[-4:]

    # --- validación y guardado ---------------------------------------------
    def clean(self):
        super().clean()
        if self.velatorio_inicio and self.velatorio_fin and self.velatorio_fin <= self.velatorio_inicio:
            raise ValidationError({"velatorio_fin": "El término del velatorio debe ser posterior al inicio."})
        if bool(self.velatorio_inicio) != bool(self.velatorio_fin):
            raise ValidationError("Indica el inicio y el término del velatorio, o deja ambos vacíos.")
        if (self.velatorio_inicio or self.sala) and not (self.sala and self.velatorio_inicio):
            raise ValidationError({"sala": "Para reservar un velatorio indica la sala y su horario."})
        if self.sala_id and not self.sala.activa:
            raise ValidationError({"sala": "La sala seleccionada no está activa."})
        if self.sala_id and self.velatorio_inicio and self.velatorio_fin:
            from . import services
            services.validar_sala(self)

    def save(self, *args, **kwargs):
        creando = self.pk is None
        with transaction.atomic():
            if self.sala_id and self.velatorio_inicio and self.velatorio_fin:
                # Bloqueo por sala: dos recepcionistas no pueden reservar el mismo horario a la vez.
                Sala.objects.select_for_update().get(pk=self.sala_id)
                from . import services
                services.validar_sala(self)
            super().save(*args, **kwargs)
            if creando:
                self.codigo = f"CR-{self.pk:04d}"
                super().save(update_fields=["codigo"])
                Documento.objects.bulk_create([Documento(solicitud=self, tipo=t) for t, _ in Documento.Tipo.choices])


class Documento(models.Model):
    class Tipo(models.TextChoices):
        CERTIFICADO = "certificado", "Certificado médico de defunción"
        CEDULA = "cedula", "Cédula de identidad del fallecido/a"
        AUTORIZACION = "autorizacion", "Autorización firmada por la familia"

    solicitud = models.ForeignKey(Solicitud, on_delete=models.CASCADE, related_name="documentos")
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    entregado = models.BooleanField(default=False)
    entregado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["solicitud", "tipo"], name="documento_unico_por_solicitud")]

    def __str__(self):
        return f"{self.get_tipo_display()} ({self.solicitud.codigo})"


# ==========================================================================
# Traslados
# ==========================================================================
class Traslado(models.Model):
    class Estado(models.TextChoices):
        PROGRAMADO = "programado", "Programado"
        EN_CAMINO = "en_camino", "En camino"
        RETRASADO = "retrasado", "Retrasado"
        COMPLETADO = "completado", "Completado"

    ACTIVOS = (Estado.PROGRAMADO, Estado.EN_CAMINO, Estado.RETRASADO)

    solicitud = models.ForeignKey(Solicitud, on_delete=models.PROTECT, related_name="traslados")
    origen = models.CharField(max_length=150)
    destino = models.CharField(max_length=150)
    inicio = models.DateTimeField("fecha y hora de salida")
    duracion_min = models.PositiveSmallIntegerField("duración (min)", default=90, validators=[MinValueValidator(10)])
    fin = models.DateTimeField(editable=False)
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, related_name="traslados")
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="traslados")
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PROGRAMADO)

    class Meta:
        ordering = ["inicio"]
        indexes = [models.Index(fields=["chofer", "inicio"]), models.Index(fields=["vehiculo", "inicio"])]

    def __str__(self):
        return f"T-{self.pk or 'nuevo'} · {self.origen} → {self.destino}"

    @property
    def codigo(self):
        return f"T-{self.pk:03d}" if self.pk else "T-nuevo"

    @property
    def ruta(self):
        return f"{self.origen} → {self.destino}"

    def calcular_fin(self):
        return self.inicio + timedelta(minutes=self.duracion_min)

    def clean(self):
        super().clean()
        if self.vehiculo_id and self.chofer_id and self.inicio and self.duracion_min:
            from . import services
            if self.estado in self.ACTIVOS:
                services.validar_traslado(self)

    def save(self, *args, **kwargs):
        self.fin = self.calcular_fin()
        if self.estado in self.ACTIVOS and self.vehiculo_id and self.chofer_id:
            from . import services
            with transaction.atomic():
                # Bloqueo del vehículo y del chofer para evitar reservas simultáneas.
                Vehiculo.objects.select_for_update().get(pk=self.vehiculo_id)
                services.validar_traslado(self)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)


# ==========================================================================
# Bodega
# ==========================================================================
class Articulo(models.Model):
    class Categoria(models.TextChoices):
        FERETROS = "feretros", "Féretros"
        URNAS = "urnas", "Urnas"
        ORNAMENTACION = "ornamentacion", "Ornamentación"
        PREPARACION = "preparacion", "Preparación"
        INSUMOS = "insumos", "Insumos"

    class Gama(models.TextChoices):
        BASICO = "basico", "Básico"
        CLASICO = "clasico", "Clásico"
        PREMIUM = "premium", "Premium"

    nombre = models.CharField(max_length=100, unique=True)
    categoria = models.CharField(max_length=15, choices=Categoria.choices)
    unidad = models.CharField(max_length=20, default="unidades")
    stock = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=1)
    gama = models.CharField("gama", max_length=10, choices=Gama.choices, blank=True,
                            help_text="Solo para féretros: básico, clásico o premium.")
    precio = models.PositiveIntegerField("precio de venta (CLP, IVA incluido)", default=0)
    descripcion = models.CharField("descripción para el sitio público", max_length=240, blank=True)
    publicado = models.BooleanField("mostrar en el sitio público", default=True,
                                    help_text="Solo se muestran féretros y urnas con precio.")
    imagen = models.ImageField("foto principal", upload_to=ruta_imagen, blank=True,
                               validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validar_peso_imagen],
                               help_text="JPG, PNG o WebP de hasta 3 MB. Si no hay foto se muestra una ilustración.")
    alto_cm = models.PositiveSmallIntegerField("alto (cm)", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(400)])
    ancho_cm = models.PositiveSmallIntegerField("ancho (cm)", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(400)])
    largo_cm = models.PositiveSmallIntegerField("largo (cm)", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(400)])
    material = models.CharField("material", max_length=60, blank=True)

    class Meta:
        ordering = ["categoria", "nombre"]
        verbose_name = "artículo"

    def __str__(self):
        return self.nombre

    @property
    def medidas(self):
        """«Largo 190 × ancho 60 × alto 55 cm» con las medidas que existan."""
        partes = [(n, v) for n, v in (("largo", self.largo_cm), ("ancho", self.ancho_cm), ("alto", self.alto_cm)) if v]
        return " × ".join(f"{n} {v}" for n, v in partes) + " cm" if partes else ""

    @property
    def estado(self):
        """stock_critico | stock_bajo | stock_normal"""
        if self.stock <= self.stock_minimo // 2:
            return "stock_critico"
        if self.stock <= self.stock_minimo:
            return "stock_bajo"
        return "stock_normal"


class Pedido(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        ENTREGADO = "entregado", "Entregado"

    solicitud = models.ForeignKey(Solicitud, on_delete=models.PROTECT, related_name="pedidos")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    creado = models.DateTimeField(auto_now_add=True)
    entregado_en = models.DateTimeField(null=True, blank=True)
    entregado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["estado", "-creado"]

    def __str__(self):
        return self.codigo

    @property
    def codigo(self):
        return f"P-{self.pk:03d}" if self.pk else "P-nuevo"


class PedidoItem(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="items")
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT, related_name="+")
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pedido", "articulo"], name="articulo_unico_por_pedido")]

    def __str__(self):
        return f"{self.cantidad} × {self.articulo}"


class Movimiento(models.Model):
    """Entrada o salida de bodega. Se crea solo mediante services.registrar_movimiento."""
    class Tipo(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SALIDA = "salida", "Salida"

    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT, related_name="movimientos")
    tipo = models.CharField(max_length=7, choices=Tipo.choices)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    motivo = models.CharField(max_length=150, blank=True)
    pedido = models.ForeignKey(Pedido, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimientos")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado", "-pk"]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.cantidad} × {self.articulo}"


# ==========================================================================
# Registro de actividad
# ==========================================================================
class Actividad(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    accion = models.CharField(max_length=200)
    objeto = models.CharField(max_length=120, blank=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creada", "-pk"]
        verbose_name = "actividad"
        verbose_name_plural = "registro de actividad"

    def __str__(self):
        return f"{self.creada:%d/%m/%Y %H:%M} · {self.accion}"


# ==========================================================================
# Precios y ventas
# ==========================================================================
MAX_FOTOS_EXTRA = 5


class ArticuloFoto(models.Model):
    """Fotos adicionales de un artículo (la principal está en Articulo.imagen)."""
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name="fotos")
    imagen = models.ImageField("foto", upload_to=ruta_imagen,
                               validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validar_peso_imagen])
    descripcion = models.CharField("descripción", max_length=80, blank=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creada", "pk"]
        verbose_name = "foto de artículo"

    def __str__(self):
        return f"Foto de {self.articulo}"


class Tarifa(models.Model):
    """Precio de cada tipo de servicio (CLP, IVA incluido)."""
    tipo_servicio = models.CharField("tipo de servicio", max_length=25, choices=Solicitud.Servicio.choices, unique=True)
    precio = models.PositiveIntegerField("precio (CLP, IVA incluido)", default=0)
    descripcion = models.CharField("qué incluye", max_length=240, blank=True)
    duracion_horas = models.PositiveSmallIntegerField("duración estimada (horas)", default=0,
                                                      validators=[MaxValueValidator(240)],
                                                      help_text="Tiempo total aproximado del servicio, desde la preparación hasta la ceremonia. 0 = sin definir.")
    activo = models.BooleanField("se ofrece", default=True, help_text="Si se desactiva, no aparece en el sitio público.")

    class Meta:
        ordering = ["precio"]
        verbose_name = "tarifa de servicio"
        verbose_name_plural = "tarifas de servicio"

    def __str__(self):
        return self.get_tipo_servicio_display()


class Venta(models.Model):
    class Tipo(models.TextChoices):
        BOLETA = "boleta", "Boleta"
        FACTURA = "factura", "Factura"

    class Pago(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        DEBITO = "debito", "Tarjeta de débito"
        CREDITO = "credito", "Tarjeta de crédito"
        TRANSFERENCIA = "transferencia", "Transferencia"

    numero = models.CharField(max_length=12, unique=True, editable=False, blank=True)
    tipo_documento = models.CharField("documento", max_length=8, choices=Tipo.choices, default=Tipo.BOLETA)
    solicitud = models.ForeignKey(Solicitud, on_delete=models.PROTECT, related_name="ventas")
    cliente_nombre = models.CharField("nombre o razón social", max_length=150)
    cliente_rut = models.CharField("RUT", max_length=12, blank=True, validators=[validar_rut])
    cliente_giro = models.CharField("giro", max_length=120, blank=True)
    cliente_direccion = models.CharField("dirección", max_length=180, blank=True)
    forma_pago = models.CharField("forma de pago", max_length=14, choices=Pago.choices, default=Pago.TRANSFERENCIA)
    neto = models.PositiveIntegerField(default=0, editable=False)
    iva = models.PositiveIntegerField(default=0, editable=False)
    total = models.PositiveIntegerField(default=0, editable=False)
    anulada = models.BooleanField(default=False, editable=False)
    anulada_en = models.DateTimeField(null=True, blank=True, editable=False)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="ventas")
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creada"]

    def __str__(self):
        return f"{self.get_tipo_documento_display()} {self.numero}"

    def clean(self):
        super().clean()
        if self.tipo_documento == self.Tipo.FACTURA:
            faltan = {c: f"La factura necesita el {n} del cliente." for c, n in
                      (("cliente_rut", "RUT"), ("cliente_giro", "giro"), ("cliente_direccion", "dirección")) if not getattr(self, c)}
            if faltan:
                raise ValidationError(faltan)

    def save(self, *args, **kwargs):
        self.cliente_rut = normalizar_rut(self.cliente_rut) if self.cliente_rut else ""
        super().save(*args, **kwargs)
        if not self.numero:
            self.numero = f"{'B' if self.tipo_documento == self.Tipo.BOLETA else 'F'}-{self.pk:06d}"
            super().save(update_fields=["numero"])


class VentaItem(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    descripcion = models.CharField(max_length=150)
    cantidad = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    precio_unitario = models.PositiveIntegerField()
    articulo = models.ForeignKey(Articulo, null=True, blank=True, on_delete=models.SET_NULL, related_name="ventas")

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def __str__(self):
        return f"{self.cantidad} × {self.descripcion}"


# ==========================================================================
# Abonos de reserva (incluye pagos con Transbank Webpay Plus)
# ==========================================================================
class Abono(models.Model):
    class Medio(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TRANSFERENCIA = "transferencia", "Transferencia"
        DEBITO = "debito", "Tarjeta de débito"
        CREDITO = "credito", "Tarjeta de crédito"
        WEBPAY = "webpay", "Webpay (Transbank)"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de pago"
        CONFIRMADO = "confirmado", "Confirmado"
        RECHAZADO = "rechazado", "Rechazado"
        ANULADO = "anulado", "Anulado"

    solicitud = models.ForeignKey(Solicitud, on_delete=models.PROTECT, related_name="abonos")
    monto = models.PositiveIntegerField("monto (CLP)", validators=[MinValueValidator(1000)])
    medio = models.CharField("medio de pago", max_length=14, choices=Medio.choices, default=Medio.TRANSFERENCIA)
    referencia = models.CharField("referencia o comprobante", max_length=80, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.CONFIRMADO)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="abonos")
    creado = models.DateTimeField(auto_now_add=True)
    # Solo para Webpay
    orden_compra = models.CharField(max_length=26, blank=True, db_index=True)
    token = models.CharField(max_length=80, blank=True, db_index=True)
    codigo_autorizacion = models.CharField(max_length=12, blank=True)
    tarjeta_ultimos4 = models.CharField(max_length=4, blank=True)

    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return f"Abono {self.monto} · {self.solicitud.codigo}"

    @property
    def cuenta(self):
        return self.estado == self.Estado.CONFIRMADO


# ==========================================================================
# Perfil del personal
# ==========================================================================
class Perfil(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    rut = models.CharField("RUT", max_length=12, blank=True, validators=[validar_rut])
    telefono = models.CharField("teléfono", max_length=30, blank=True, validators=[validar_telefono])
    cargo = models.CharField(max_length=60, blank=True)
    direccion = models.CharField("dirección", max_length=150, blank=True)
    contacto_emergencia = models.CharField("contacto de emergencia", max_length=120, blank=True,
                                           help_text="Nombre y teléfono.")

    class Meta:
        verbose_name_plural = "perfiles"

    def __str__(self):
        return f"Perfil de {self.usuario}"

    def save(self, *args, **kwargs):
        self.rut = normalizar_rut(self.rut) if self.rut else ""
        super().save(*args, **kwargs)


# ==========================================================================
# Ausencias del personal
# ==========================================================================
class Ausencia(models.Model):
    class Tipo(models.TextChoices):
        PERMISO = "permiso", "Permiso"
        VACACIONES = "vacaciones", "Vacaciones"
        LICENCIA = "licencia", "Licencia médica"
        OTRO = "otro", "Otro"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        APROBADA = "aprobada", "Aprobada"
        RECHAZADA = "rechazada", "Rechazada"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ausencias")
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.PERMISO)
    desde = models.DateField("desde")
    hasta = models.DateField("hasta (incluido)")
    motivo = models.CharField(max_length=200, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    resuelta_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="ausencias_resueltas")
    resuelta_en = models.DateTimeField(null=True, blank=True)
    comentario = models.CharField("comentario del administrador", max_length=200, blank=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["desde", "usuario__first_name"]
        indexes = [models.Index(fields=["usuario", "desde"]), models.Index(fields=["estado", "desde"])]

    def __str__(self):
        return f"{self.usuario} · {self.get_tipo_display()} {self.desde:%d/%m} al {self.hasta:%d/%m}"

    @property
    def dias(self):
        return (self.hasta - self.desde).days + 1

    @property
    def estado_clave(self):
        """Clave para la insignia: pendiente | aprobada | rechazada"""
        return self.estado

    def clean(self):
        super().clean()
        if self.desde and self.hasta:
            if self.hasta < self.desde:
                raise ValidationError({"hasta": "La fecha de término no puede ser anterior al inicio."})
            if self.dias > 60:
                raise ValidationError({"hasta": "Una ausencia no puede durar más de 60 días: divídela en varias."})
            if self.usuario_id:
                choque = Ausencia.objects.filter(usuario_id=self.usuario_id, desde__lte=self.hasta, hasta__gte=self.desde
                                                 ).exclude(estado=self.Estado.RECHAZADA).exclude(pk=self.pk).first()
                if choque:
                    raise ValidationError(f"Ya existe una ausencia ({choque.get_estado_display().lower()}) que cubre esas fechas: del {choque.desde:%d/%m} al {choque.hasta:%d/%m}.")
