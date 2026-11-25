"""
Carga datos de demostración: usuarios por rol, salas, flota, inventario, solicitudes, traslados y pedidos.
Todas las personas y datos son ficticios. Las fechas se calculan a partir del momento en que se ejecuta.

Uso:
    python manage.py seed_demo
"""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.utils import timezone

from servicios import services
from servicios.models import (Articulo, Documento, Pedido, PedidoItem, Sala, Solicitud, Tarifa, Traslado, Vehiculo)

USUARIOS = [
    # username, clave, nombre, apellido, grupo (None = administrador)
    ("admin", "Admin12345!", "Rodrigo", "Valdés", None),
    ("msoto", "Recepcion123!", "Marcela", "Soto", "Recepcionista"),
    ("paravena", "Recepcion123!", "Paula", "Aravena", "Recepcionista"),
    ("hcastillo", "Bodega12345!", "Hernán", "Castillo", "Bodega"),
    ("lmaldonado", "Chofer12345!", "Luis", "Maldonado", "Chofer"),
    ("jsepulveda", "Chofer12345!", "Jorge", "Sepúlveda", "Chofer"),
]

ARTICULOS = [
    # nombre, categoría, stock, mínimo, unidad, gama, precio (CLP, IVA incluido)
    ("Féretro básico", "feretros", 6, 3, "unidades", "basico", 280000),
    ("Féretro clásico", "feretros", 7, 3, "unidades", "clasico", 520000),
    ("Féretro premium", "feretros", 2, 2, "unidades", "premium", 980000),
    ("Urna para cenizas estándar", "urnas", 1, 4, "unidades", "", 95000),
    ("Urna de madera", "urnas", 5, 2, "unidades", "", 145000),
    ("Urna premium", "urnas", 3, 2, "unidades", "", 260000),
    ("Corona de flores", "ornamentacion", 9, 4, "unidades", "", 45000),
    ("Cirios", "ornamentacion", 38, 20, "unidades", "", 2500),
    ("Mortaja y vestimenta", "preparacion", 10, 10, "unidades", "", 60000),
    ("Líquido de conservación", "preparacion", 4, 12, "litros", "", 0),
    ("Guantes y mascarillas", "insumos", 18, 8, "cajas", "", 0),
]
DESCRIPCIONES = {
    "Féretro básico": "Madera aglomerada con terminación sobria. Una opción digna y accesible.",
    "Féretro clásico": "Madera sólida con barniz y manillas metálicas. El más elegido por las familias.",
    "Féretro premium": "Madera noble de terminación artesanal, interior acolchado y herrajes de primera calidad.",
    "Urna para cenizas estándar": "Urna sencilla de material resistente, con cierre seguro.",
    "Urna de madera": "Urna de madera barnizada con tapa de cierre y placa para grabar un nombre.",
    "Urna premium": "Urna de madera noble con detalles artesanales y placa grabada incluida.",
}
TARIFAS = {"velatorio_sepultacion": 890000, "cremacion": 650000, "traslado_cremacion": 720000, "velatorio_cremacion": 980000}


class Command(BaseCommand):
    help = "Carga datos y usuarios de demostración (todo ficticio)."

    def handle(self, *args, **opts):
        users = {}
        for username, clave, nombre, apellido, grupo in USUARIOS:
            u, nuevo = User.objects.get_or_create(username=username, defaults={
                "first_name": nombre, "last_name": apellido, "email": f"{username}@cristorey.example"})
            if nuevo:
                u.set_password(clave)
                if grupo is None:
                    u.is_staff = u.is_superuser = True
                u.save()
            if grupo:
                u.groups.add(Group.objects.get(name=grupo))
            users[username] = u

        if Solicitud.objects.exists():
            self.stdout.write("Ya hay solicitudes cargadas: solo se aseguraron los usuarios.")
            return self._resumen()

        ahora = timezone.now()
        h = lambda n: ahora + timedelta(hours=n)
        d = lambda n, hora=10, minuto=0: (ahora + timedelta(days=n)).replace(hour=hora, minute=minuto, second=0, microsecond=0)

        salas = [Sala.objects.create(nombre=n, capacidad=c) for n, c in (("Sala 1", 30), ("Sala 2", 50), ("Sala 3", 80))]
        carroza1 = Vehiculo.objects.create(nombre="Carroza 1", patente="BKLT-21", tipo="carroza")
        carroza2 = Vehiculo.objects.create(nombre="Carroza 2", patente="RTXD-48", tipo="carroza", estado="en_ruta")
        furgon = Vehiculo.objects.create(nombre="Furgón de traslado", patente="HJPC-73", tipo="furgon", estado="en_ruta")
        Vehiculo.objects.create(nombre="Vehículo de apoyo", patente="LNZV-05", tipo="apoyo", estado="fuera_servicio")
        art = {a[0]: Articulo.objects.create(nombre=a[0], categoria=a[1], stock=a[2], stock_minimo=a[3], unidad=a[4], gama=a[5], precio=a[6],
                                             descripcion=DESCRIPCIONES.get(a[0], "")) for a in ARTICULOS}
        for tipo, precio in TARIFAS.items():
            Tarifa.objects.update_or_create(tipo_servicio=tipo, defaults={"precio": precio})

        def solicitud(fallecido, contacto, tel, email, servicio, ceremonia, lugar, etapa, resp, docs, sala=None, vel=None, feretro=None):
            s = Solicitud.objects.create(
                fallecido=fallecido, contacto_nombre=contacto, contacto_telefono=tel, contacto_email=email, tipo_servicio=servicio,
                ceremonia_inicio=ceremonia, lugar_ceremonia=lugar, etapa=etapa, responsable=users[resp],
                sala=sala, velatorio_inicio=vel[0] if vel else None, velatorio_fin=vel[1] if vel else None,
                feretro=art[feretro] if feretro else None)
            for tipo in docs:
                Documento.objects.filter(solicitud=s, tipo=tipo).update(entregado=True, entregado_en=ahora)
            return s

        todos = ["certificado", "cedula", "autorizacion"]
        parroquial, crematorio = "Cementerio Parroquial San José", "Crematorio Municipal"
        s1 = solicitud("María Eugenia Rojas Pino", "Carolina Pino (hija)", "+56 9 5555 0142", "carolina.pino@example.com", "velatorio_sepultacion",
                       h(5), parroquial, 3, "msoto", ["certificado", "cedula"], salas[1], (h(-30), h(4)), "Féretro clásico")
        s2 = solicitud("Héctor Antonio Salinas Mora", "Patricio Salinas (hijo)", "+56 9 5555 0143", "patricio.salinas@example.com", "cremacion",
                       d(2, 10, 30), crematorio, 2, "paravena", todos, salas[0], (h(-24), d(1, 20)), "Féretro básico")
        s3 = solicitud("Rosa Elvira Contreras Vega", "Daniela Vega (sobrina)", "+56 9 5555 0144", "daniela.vega@example.com", "velatorio_sepultacion",
                       d(3, 15), parroquial, 1, "msoto", ["certificado"], salas[2], (d(1, 12), d(2, 20)), "Féretro premium")
        s4 = solicitud("Juan Bautista Herrera Díaz", "Marta Díaz (esposa)", "+56 9 5555 0145", "marta.diaz@example.com", "traslado_cremacion",
                       d(4, 12), crematorio, 0, "paravena", todos, feretro="Féretro básico")
        solicitud("Manuel Jesús Ortiz Fuentes", "Sandra Fuentes (hermana)", "+56 9 5555 0146", "sandra.fuentes@example.com", "velatorio_cremacion",
                  d(6, 11), crematorio, 0, "msoto", [])
        s6 = solicitud("Elena Mercedes Araya Lagos", "Ricardo Araya (hijo)", "+56 9 5555 0141", "ricardo.araya@example.com", "velatorio_sepultacion",
                       ahora - timedelta(days=1), parroquial, 5, "paravena", todos, feretro="Féretro clásico")

        luis, jorge = users["lmaldonado"], users["jsepulveda"]
        t = lambda sol, o, dst, ini, veh, ch, est="programado": Traslado.objects.create(
            solicitud=sol, origen=o, destino=dst, inicio=ini, vehiculo=veh, chofer=ch, estado=est)
        t(s1, "Hospital Regional", "Sala de velatorio Cristo Rey", h(-30), furgon, luis, "completado")
        t(s1, "Sala de velatorio Cristo Rey", parroquial, h(3), carroza1, luis)
        t(s2, "Domicilio", "Sala de velatorio Cristo Rey", h(-1), furgon, luis, "retrasado")
        t(s4, "Clínica Los Andes", "Sala de preparación", h(-0.5), carroza2, jorge, "en_camino")
        t(s3, "Domicilio", "Sala de preparación", d(1, 8), carroza1, jorge)
        t(s2, "Sala de velatorio Cristo Rey", crematorio, d(2, 9), carroza1, jorge)

        def pedido(sol, items, creador="hcastillo"):
            p = Pedido.objects.create(solicitud=sol, creado_por=users[creador])
            PedidoItem.objects.bulk_create([PedidoItem(pedido=p, articulo=art[n], cantidad=c) for n, c in items])
            return p
        pedido(s1, [("Féretro clásico", 1), ("Corona de flores", 2)])
        pedido(s2, [("Urna para cenizas estándar", 1)])
        pedido(s3, [("Féretro clásico", 1), ("Mortaja y vestimenta", 1)])
        services.entregar_pedido(pedido(s6, [("Féretro clásico", 1)]), users["hcastillo"])

        # Ventas de ejemplo: una boleta (servicio finalizado) y una factura a una empresa ficticia
        services.crear_venta(s6, users["paravena"], {"tipo_documento": "boleta", "cliente_nombre": "Ricardo Araya", "cliente_rut": "",
                                                    "forma_pago": "debito"},
                             [("Velatorio y sepultación", 1, TARIFAS["velatorio_sepultacion"], None), ("Féretro clásico", 1, 520000, art["Féretro clásico"])])
        services.crear_venta(s2, users["msoto"], {"tipo_documento": "factura", "cliente_nombre": "Seguros Ejemplo SpA", "cliente_rut": "76543210-3",
                                                 "cliente_giro": "Seguros generales", "cliente_direccion": "Av. Ejemplo 123, Temuco",
                                                 "forma_pago": "transferencia"},
                             [("Cremación", 1, TARIFAS["cremacion"], None), ("Féretro básico", 1, 280000, art["Féretro básico"])])

        # Ventas de meses anteriores, para que el gráfico del panel tenga historia
        from servicios.models import Venta
        for dias_atras, lineas, user in ((35, [("Cremación", 1, TARIFAS["cremacion"], None)], "msoto"),
                                         (62, [("Velatorio y sepultación", 1, TARIFAS["velatorio_sepultacion"], None), ("Féretro básico", 1, 280000, art["Féretro básico"])], "paravena"),
                                         (95, [("Velatorio y cremación", 1, TARIFAS["velatorio_cremacion"], None)], "msoto"),
                                         (130, [("Traslado y cremación", 1, TARIFAS["traslado_cremacion"], None), ("Urna de madera", 1, 145000, art["Urna de madera"])], "paravena")):
            v = services.crear_venta(s6, users[user], {"tipo_documento": "boleta", "cliente_nombre": "Cliente de ejemplo", "forma_pago": "transferencia"}, lineas)
            Venta.objects.filter(pk=v.pk).update(creada=ahora - timedelta(days=dias_atras))

        # Ausencias de ejemplo: una hoy (aprobada), vacaciones próximas y dos solicitudes por revisar
        hoy = timezone.localdate()
        dia = lambda n: hoy + timedelta(days=n)
        admin = users["admin"]
        services.solicitar_ausencia(users["paravena"], {"tipo": "permiso", "desde": hoy, "hasta": hoy, "motivo": "Trámite personal"}, por=admin)
        services.solicitar_ausencia(users["lmaldonado"], {"tipo": "vacaciones", "desde": dia(10), "hasta": dia(14), "motivo": "Vacaciones de verano"}, por=admin)
        services.solicitar_ausencia(users["jsepulveda"], {"tipo": "permiso", "desde": dia(7), "hasta": dia(8), "motivo": "Matrimonio de un familiar"})
        services.solicitar_ausencia(users["hcastillo"], {"tipo": "licencia", "desde": dia(3), "hasta": dia(5), "motivo": "Control médico"})

        self._resumen()

    def _resumen(self):
        self.stdout.write(self.style.SUCCESS("Datos de demostración listos."))
        self.stdout.write("Usuarios: " + ", ".join(f"{u[0]} / {u[1]}" for u in USUARIOS))
