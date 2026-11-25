from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from io import BytesIO

from django.core.management import call_command
from openpyxl import load_workbook
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import (Actividad, Articulo, Documento, Movimiento, Pedido, PedidoItem, Sala, Solicitud, Tarifa, Traslado, Vehiculo, Venta,
                     validar_rut)

H = lambda n: timezone.now() + timedelta(hours=n)


def usuario(nombre, grupo=None, admin=False):
    u = User.objects.create_user(nombre, f"{nombre}@example.com", "Clave12345!", first_name=nombre.title(), last_name="Prueba")
    if admin:
        u.is_staff = u.is_superuser = True
        u.save()
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = usuario("admin", admin=True)
        cls.recep = usuario("recep", "Recepcionista")
        cls.bodega = usuario("bodega", "Bodega")
        cls.chofer = usuario("chofer1", "Chofer")
        cls.chofer2 = usuario("chofer2", "Chofer")
        cls.sala1 = Sala.objects.create(nombre="Sala 1", capacidad=30)
        cls.sala2 = Sala.objects.create(nombre="Sala 2", capacidad=50)
        cls.carroza = Vehiculo.objects.create(nombre="Carroza 1", patente="AAAA-11", tipo="carroza")
        cls.furgon = Vehiculo.objects.create(nombre="Furgón", patente="BBBB-22", tipo="furgon")
        cls.feretro = Articulo.objects.create(nombre="Féretro", categoria="feretros", stock=5, stock_minimo=2, unidad="unidades")
        cls.cirios = Articulo.objects.create(nombre="Cirios", categoria="ornamentacion", stock=10, stock_minimo=4, unidad="unidades")

    def setUp(self):
        cache.clear()

    def solicitud(self, **kw):
        datos = dict(fallecido="Persona Ficticia", contacto_nombre="Contacto", contacto_telefono="+56 9 5555 1234",
                     contacto_email="familia@example.com", tipo_servicio="cremacion", ceremonia_inicio=H(72),
                     lugar_ceremonia="Crematorio Municipal", responsable=self.recep)
        datos.update(kw)
        return Solicitud.objects.create(**datos)

    def entregar_todo(self, s):
        s.documentos.update(entregado=True, entregado_en=timezone.now())

    def traslado(self, s=None, inicio=None, vehiculo=None, chofer=None, **kw):
        return Traslado.objects.create(solicitud=s or self.solicitud(), origen="Domicilio", destino="Sala", inicio=inicio or H(10),
                                       vehiculo=vehiculo or self.carroza, chofer=chofer or self.chofer, **kw)


# --------------------------------------------------------------------------
class SolicitudTests(Base):
    def test_codigo_y_documentos_automaticos(self):
        s = self.solicitud()
        self.assertEqual(s.codigo, f"CR-{s.pk:04d}")
        self.assertEqual(s.documentos.count(), 3)

    def test_verificacion_son_ultimos_4_digitos(self):
        self.assertEqual(self.solicitud(contacto_telefono="+56 9 5555 0142").verificacion_familia, "0142")

    def test_estado_docs_pendientes_en_curso_finalizada_urgente(self):
        s = self.solicitud(ceremonia_inicio=H(72))
        self.assertEqual(s.estado, "docs_pendientes")
        self.entregar_todo(s)
        self.assertEqual(Solicitud.objects.get(pk=s.pk).estado, "en_curso")
        u = self.solicitud(ceremonia_inicio=H(5))
        self.assertEqual(u.estado, "urgente")
        f = self.solicitud(etapa=Solicitud.Etapa.FINALIZADO)
        self.assertEqual(f.estado, "finalizada")

    def test_velatorio_fin_debe_ser_posterior(self):
        s = Solicitud(fallecido="X", contacto_nombre="C", contacto_telefono="+56 9 5555 1234", tipo_servicio="cremacion",
                      ceremonia_inicio=H(50), lugar_ceremonia="L", sala=self.sala1, velatorio_inicio=H(10), velatorio_fin=H(5))
        with self.assertRaises(ValidationError):
            s.full_clean()

    def test_telefono_invalido(self):
        s = Solicitud(fallecido="X", contacto_nombre="C", contacto_telefono="abc", tipo_servicio="cremacion",
                      ceremonia_inicio=H(50), lugar_ceremonia="L")
        with self.assertRaises(ValidationError):
            s.full_clean()


class SalaTests(Base):
    def test_no_se_puede_solapar_la_sala(self):
        self.solicitud(sala=self.sala1, velatorio_inicio=H(10), velatorio_fin=H(20))
        with self.assertRaises(ValidationError):
            self.solicitud(sala=self.sala1, velatorio_inicio=H(15), velatorio_fin=H(25))

    def test_otra_sala_o_horario_contiguo_si_se_puede(self):
        self.solicitud(sala=self.sala1, velatorio_inicio=H(10), velatorio_fin=H(20))
        self.solicitud(sala=self.sala2, velatorio_inicio=H(15), velatorio_fin=H(25))
        self.solicitud(sala=self.sala1, velatorio_inicio=H(20), velatorio_fin=H(30))

    def test_editar_la_misma_solicitud_no_choca_consigo_misma(self):
        s = self.solicitud(sala=self.sala1, velatorio_inicio=H(10), velatorio_fin=H(20))
        s.lugar_ceremonia = "Otro lugar"
        s.full_clean()
        s.save()

    def test_solicitud_finalizada_libera_la_sala(self):
        self.solicitud(sala=self.sala1, velatorio_inicio=H(10), velatorio_fin=H(20), etapa=Solicitud.Etapa.FINALIZADO)
        self.solicitud(sala=self.sala1, velatorio_inicio=H(12), velatorio_fin=H(18))


class EtapaTests(Base):
    def test_no_avanza_a_ceremonia_con_documentos_pendientes(self):
        s = self.solicitud(etapa=Solicitud.Etapa.TRASLADO)
        with self.assertRaises(ValidationError):
            services.avanzar_etapa(s, self.recep)
        s.refresh_from_db()
        self.assertEqual(s.etapa, Solicitud.Etapa.TRASLADO)

    def test_avanza_con_documentos_completos_hasta_finalizar(self):
        s = self.solicitud(etapa=Solicitud.Etapa.TRASLADO)
        self.entregar_todo(s)
        s = services.avanzar_etapa(s, self.recep)
        self.assertEqual(s.etapa, Solicitud.Etapa.CEREMONIA)
        s = services.avanzar_etapa(s, self.recep)
        self.assertTrue(s.finalizada)
        with self.assertRaises(ValidationError):
            services.avanzar_etapa(s, self.recep)

    def test_marcar_documento_registra_actividad(self):
        d = self.solicitud().documentos.first()
        services.marcar_documento(d, True, self.recep)
        d.refresh_from_db()
        self.assertTrue(d.entregado)
        self.assertTrue(Actividad.objects.filter(usuario=self.recep).exists())


# --------------------------------------------------------------------------
class TrasladoTests(Base):
    def test_vehiculo_no_puede_solaparse(self):
        self.traslado(inicio=H(10))
        with self.assertRaises(ValidationError):
            self.traslado(inicio=H(10.5), chofer=self.chofer2)

    def test_chofer_no_puede_solaparse(self):
        self.traslado(inicio=H(10))
        with self.assertRaises(ValidationError):
            self.traslado(inicio=H(10.5), vehiculo=self.furgon)

    def test_mismo_horario_con_otro_vehiculo_y_chofer_si(self):
        self.traslado(inicio=H(10))
        self.traslado(inicio=H(10), vehiculo=self.furgon, chofer=self.chofer2)

    def test_traslado_completado_no_bloquea(self):
        self.traslado(inicio=H(10), estado="completado")
        self.traslado(inicio=H(10))

    def test_vehiculo_en_mantencion_no_sirve(self):
        self.carroza.estado = "mantencion"
        self.carroza.save()
        with self.assertRaises(ValidationError):
            self.traslado()

    def test_solo_un_chofer_activo_puede_conducir(self):
        with self.assertRaises(ValidationError):
            self.traslado(chofer=self.recep)

    def test_transiciones_validas_e_invalidas(self):
        t = self.traslado()
        with self.assertRaises(ValidationError):
            services.cambiar_estado_traslado(t, "completado", self.chofer)
        t = services.cambiar_estado_traslado(t, "en_camino", self.chofer)
        self.carroza.refresh_from_db()
        self.assertEqual(self.carroza.estado, "en_ruta")
        t = services.cambiar_estado_traslado(t, "completado", self.chofer)
        self.carroza.refresh_from_db()
        self.assertEqual(self.carroza.estado, "disponible")
        with self.assertRaises(ValidationError):
            services.cambiar_estado_traslado(t, "en_camino", self.chofer)


# --------------------------------------------------------------------------
class StockTests(Base):
    def test_entrada_y_salida(self):
        services.registrar_movimiento(self.feretro.pk, "entrada", 3, self.bodega)
        services.registrar_movimiento(self.feretro.pk, "salida", 2, self.bodega)
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 6)
        self.assertEqual(Movimiento.objects.count(), 2)

    def test_stock_nunca_negativo(self):
        with self.assertRaises(ValidationError):
            services.registrar_movimiento(self.feretro.pk, "salida", 6, self.bodega)
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 5)
        self.assertEqual(Movimiento.objects.count(), 0)

    def test_cantidad_invalida(self):
        with self.assertRaises(ValidationError):
            services.registrar_movimiento(self.feretro.pk, "entrada", 0, self.bodega)

    def test_estado_de_stock(self):
        a = Articulo(nombre="A", categoria="insumos", stock=10, stock_minimo=4, unidad="u")
        self.assertEqual(a.estado, "stock_normal")
        a.stock = 4
        self.assertEqual(a.estado, "stock_bajo")
        a.stock = 2
        self.assertEqual(a.estado, "stock_critico")

    def pedido(self, *items):
        p = Pedido.objects.create(solicitud=self.solicitud(), creado_por=self.bodega)
        for art, cant in items:
            PedidoItem.objects.create(pedido=p, articulo=art, cantidad=cant)
        return p

    def test_entregar_pedido_descuenta_todo(self):
        p = services.entregar_pedido(self.pedido((self.feretro, 1), (self.cirios, 4)), self.bodega)
        self.feretro.refresh_from_db(), self.cirios.refresh_from_db()
        self.assertEqual((self.feretro.stock, self.cirios.stock), (4, 6))
        self.assertEqual(p.estado, Pedido.Estado.ENTREGADO)

    def test_pedido_todo_o_nada(self):
        p = self.pedido((self.cirios, 4), (self.feretro, 9))
        with self.assertRaises(ValidationError):
            services.entregar_pedido(p, self.bodega)
        self.feretro.refresh_from_db(), self.cirios.refresh_from_db()
        self.assertEqual((self.feretro.stock, self.cirios.stock), (5, 10))
        p.refresh_from_db()
        self.assertEqual(p.estado, Pedido.Estado.PENDIENTE)

    def test_pedido_no_se_entrega_dos_veces(self):
        p = services.entregar_pedido(self.pedido((self.feretro, 1)), self.bodega)
        with self.assertRaises(ValidationError):
            services.entregar_pedido(p, self.bodega)
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 4)


# --------------------------------------------------------------------------
class VistasPorRolTests(Base):
    def entrar(self, u):
        self.client.force_login(u)

    def test_login_requerido(self):
        for nombre in ("solicitud_list", "calendario", "venta_list", "precios"):
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn("login", r.url)

    def test_home_responde_para_cada_rol(self):
        for u in (self.admin, self.recep, self.bodega, self.chofer):
            self.entrar(u)
            self.assertEqual(self.client.get(reverse("home")).status_code, 200, u.username)

    def test_matriz_de_acceso(self):
        s = self.solicitud()
        rutas = {
            "solicitud_list": ({"admin", "recep"}, {}),
            "solicitud_create": ({"admin", "recep"}, {}),
            "solicitud_detail": ({"admin", "recep"}, {"pk": s.pk}),
            "sala_agenda": ({"admin", "recep"}, {}),
            "traslado_list": ({"admin", "recep", "chofer1"}, {}),
            "traslado_create": ({"admin", "recep"}, {}),
            "vehiculo_list": ({"admin"}, {}),
            "vehiculo_create": ({"admin"}, {}),
            "articulo_list": ({"admin", "bodega"}, {}),
            "articulo_create": ({"admin", "bodega"}, {}),
            "movimiento_list": ({"admin", "bodega"}, {}),
            "pedido_list": ({"admin", "bodega"}, {}),
            "pedido_create": ({"admin", "bodega"}, {}),
            "usuario_list": ({"admin"}, {}),
            "usuario_create": ({"admin"}, {}),
            "actividad_list": ({"admin"}, {}),
        }
        usuarios = {"admin": self.admin, "recep": self.recep, "bodega": self.bodega, "chofer1": self.chofer}
        for nombre, (permitidos, kw) in rutas.items():
            for uname, u in usuarios.items():
                self.client.force_login(u)
                codigo = self.client.get(reverse(nombre, kwargs=kw)).status_code
                esperado = 200 if uname in permitidos else 403
                self.assertEqual(codigo, esperado, f"{nombre} como {uname}: {codigo}")

    def test_recepcion_crea_solicitud(self):
        self.entrar(self.recep)
        r = self.client.post(reverse("solicitud_create"), {
            "fallecido": "Nueva Persona", "contacto_nombre": "Familiar", "contacto_telefono": "+56 9 5555 9999",
            "contacto_email": "f@example.com", "tipo_servicio": "cremacion", "ceremonia_inicio": H(80).strftime("%Y-%m-%dT%H:%M"),
            "lugar_ceremonia": "Crematorio", "responsable": self.recep.pk, "etapa": 0})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.assertTrue(Solicitud.objects.filter(fallecido="Nueva Persona").exists())

    def test_recepcion_no_elimina_solicitud_pero_admin_si(self):
        s = self.solicitud()
        self.entrar(self.recep)
        self.assertEqual(self.client.post(reverse("solicitud_delete", args=[s.pk])).status_code, 403)
        self.entrar(self.admin)
        self.client.post(reverse("solicitud_delete", args=[s.pk]))
        self.assertFalse(Solicitud.objects.filter(pk=s.pk).exists())

    def test_marcar_documento_y_avanzar_desde_la_vista(self):
        s = self.solicitud(etapa=Solicitud.Etapa.TRASLADO)
        self.entrar(self.recep)
        self.client.post(reverse("solicitud_avanzar", args=[s.pk]), follow=True)
        s.refresh_from_db()
        self.assertEqual(s.etapa, Solicitud.Etapa.TRASLADO)  # bloqueado por documentos
        for d in s.documentos.all():
            self.client.post(reverse("documento_marcar", args=[s.pk, d.pk]), {"entregado": "1"})
        self.client.post(reverse("solicitud_avanzar", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.etapa, Solicitud.Etapa.CEREMONIA)

    def test_chofer_solo_ve_y_actualiza_sus_traslados(self):
        propio = self.traslado(inicio=H(10))
        ajeno = self.traslado(inicio=H(10), vehiculo=self.furgon, chofer=self.chofer2)
        self.entrar(self.chofer)
        r = self.client.get(reverse("traslado_list"))
        self.assertContains(r, propio.codigo)
        self.assertNotContains(r, ajeno.codigo)
        self.assertIn(self.client.post(reverse("traslado_estado", args=[ajeno.pk]), {"accion": "iniciar"}).status_code, (403, 404))
        self.client.post(reverse("traslado_estado", args=[propio.pk]), {"accion": "iniciar"})
        propio.refresh_from_db()
        self.assertEqual(propio.estado, "en_camino")

    def test_bodega_registra_movimiento_y_no_puede_dejar_stock_negativo(self):
        self.entrar(self.bodega)
        self.client.post(reverse("movimiento_create"), {"articulo": self.feretro.pk, "tipo": "salida", "cantidad": 2, "motivo": "uso"})
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 3)
        self.client.post(reverse("movimiento_create"), {"articulo": self.feretro.pk, "tipo": "salida", "cantidad": 50, "motivo": "x"})
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 3)

    def test_solo_admin_crea_usuarios(self):
        datos = {"username": "nuevo", "first_name": "Nuevo", "last_name": "Usuario", "email": "n@example.com",
                 "password1": "ClaveSegura789!", "password2": "ClaveSegura789!", "rol": "chofer"}
        self.entrar(self.recep)
        self.assertEqual(self.client.post(reverse("usuario_create"), datos).status_code, 403)
        self.assertFalse(User.objects.filter(username="nuevo").exists())
        self.entrar(self.admin)
        self.client.post(reverse("usuario_create"), datos)
        nuevo = User.objects.get(username="nuevo")
        self.assertTrue(nuevo.groups.filter(name="Chofer").exists())
        self.assertFalse(nuevo.is_superuser)

    def test_pagina_404_personalizada(self):
        self.entrar(self.admin)
        self.assertEqual(self.client.get("/no-existe/").status_code, 404)


# --------------------------------------------------------------------------
class ApiTests(Base):
    def cliente(self, u):
        c = APIClient()
        c.force_authenticate(u)
        return c

    def test_sin_autenticar_401_o_403(self):
        self.assertIn(APIClient().get("/api/solicitudes/").status_code, (401, 403))

    def test_matriz_de_lectura(self):
        permitido = {
            "solicitudes": {"admin", "recep"}, "salas": {"admin", "recep"}, "vehiculos": {"admin", "recep"},
            "traslados": {"admin", "recep", "chofer1"}, "articulos": {"admin", "recep", "bodega"},
            "movimientos": {"admin", "bodega"}, "pedidos": {"admin", "recep", "bodega"}, "actividad": {"admin"},
        }
        usuarios = {"admin": self.admin, "recep": self.recep, "bodega": self.bodega, "chofer1": self.chofer}
        for recurso, ok in permitido.items():
            for nombre, u in usuarios.items():
                r = self.cliente(u).get(f"/api/{recurso}/")
                self.assertEqual(r.status_code, 200 if nombre in ok else 403, f"{recurso} como {nombre}")

    def test_regla_de_negocio_devuelve_400(self):
        c = self.cliente(self.bodega)
        r = c.post("/api/movimientos/", {"articulo": self.feretro.pk, "tipo": "salida", "cantidad": 99}, format="json")
        self.assertEqual(r.status_code, 400)
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 5)

    def test_recepcion_no_escribe_inventario_ni_bodega_solicitudes(self):
        self.assertEqual(self.cliente(self.recep).post("/api/articulos/", {"nombre": "X"}, format="json").status_code, 403)
        self.assertEqual(self.cliente(self.bodega).post("/api/solicitudes/", {}, format="json").status_code, 403)

    def test_chofer_solo_ve_sus_traslados_y_no_crea(self):
        propio = self.traslado(inicio=H(10))
        ajeno = self.traslado(inicio=H(10), vehiculo=self.furgon, chofer=self.chofer2)
        c = self.cliente(self.chofer)
        ids = [t["id"] for t in c.get("/api/traslados/").json()["results"]]
        self.assertEqual(ids, [propio.pk])
        self.assertEqual(c.get(f"/api/traslados/{ajeno.pk}/").status_code, 404)
        self.assertEqual(c.post("/api/traslados/", {}, format="json").status_code, 403)
        r = c.post(f"/api/traslados/{propio.pk}/estado/", {"accion": "iniciar"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.post(f"/api/traslados/{propio.pk}/estado/", {"accion": "iniciar"}, format="json").status_code, 400)

    def test_avanzar_etapa_bloqueado_por_documentos(self):
        s = self.solicitud(etapa=Solicitud.Etapa.TRASLADO)
        r = self.cliente(self.recep).post(f"/api/solicitudes/{s.pk}/avanzar/")
        self.assertEqual(r.status_code, 400)

    def test_solo_admin_elimina_solicitud(self):
        s = self.solicitud()
        self.assertEqual(self.cliente(self.recep).delete(f"/api/solicitudes/{s.pk}/").status_code, 403)
        self.assertEqual(self.cliente(self.admin).delete(f"/api/solicitudes/{s.pk}/").status_code, 204)


# --------------------------------------------------------------------------
class FamiliaTests(Base):
    def test_codigo_y_telefono_correctos(self):
        s = self.solicitud(contacto_telefono="+56 9 5555 0142")
        r = self.client.post(reverse("familia"), {"codigo": s.codigo.lower(), "verificacion": "0142"})
        self.assertContains(r, s.fallecido)

    def test_telefono_incorrecto_no_muestra_nada(self):
        s = self.solicitud(contacto_telefono="+56 9 5555 0142")
        r = self.client.post(reverse("familia"), {"codigo": s.codigo, "verificacion": "9999"})
        self.assertNotContains(r, s.fallecido)
        self.assertContains(r, "No encontramos")

    def test_no_expone_datos_internos(self):
        s = self.solicitud(contacto_telefono="+56 9 5555 0142")
        r = self.client.post(reverse("familia"), {"codigo": s.codigo, "verificacion": "0142"})
        self.assertNotContains(r, "5555 0142")
        self.assertNotContains(r, "familia@example.com")

    def test_limite_de_intentos(self):
        s = self.solicitud(contacto_telefono="+56 9 5555 0142")
        for _ in range(8):
            self.client.post(reverse("familia"), {"codigo": s.codigo, "verificacion": "0000"})
        r = self.client.post(reverse("familia"), {"codigo": s.codigo, "verificacion": "0142"})
        self.assertNotContains(r, s.fallecido)
        self.assertContains(r, "Demasiados intentos")


# --------------------------------------------------------------------------
class AvisosTests(Base):
    def test_avisa_solo_a_quien_corresponde_y_una_vez(self):
        pendiente = self.solicitud(ceremonia_inicio=H(24))
        completa = self.solicitud(ceremonia_inicio=H(24))
        self.entregar_todo(completa)
        self.solicitud(ceremonia_inicio=H(24 * 20))  # muy lejos
        self.solicitud(ceremonia_inicio=H(24), contacto_email="")  # sin correo
        self.assertEqual(services.avisar_documentos(), (1, 0))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(pendiente.codigo, mail.outbox[0].body)
        self.assertEqual(services.avisar_documentos(), (0, 0))  # no repite en 24 h

    def test_simular_no_envia(self):
        self.solicitud(ceremonia_inicio=H(24))
        self.assertEqual(services.avisar_documentos(simular=True), (1, 0))
        self.assertEqual(len(mail.outbox), 0)


class SeedTests(TestCase):
    def test_seed_demo_es_repetible(self):
        call_command("seed_demo", verbosity=0)
        n = Solicitud.objects.count()
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Solicitud.objects.count(), n)
        self.assertEqual(n, 6)
        self.assertEqual(Solicitud.objects.get(codigo="CR-0001").estado, "urgente")


# --------------------------------------------------------------------------
# Precios, buscador, Excel, calendario y ventas
# --------------------------------------------------------------------------
class Precios(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Articulo.objects.filter(pk=cls.feretro.pk).update(gama="clasico", precio=520000)
        cls.feretro.refresh_from_db()
        cls.basico = Articulo.objects.create(nombre="Féretro básico", categoria="feretros", gama="basico", precio=280000, stock=3, stock_minimo=1)
        Tarifa.objects.create(tipo_servicio="cremacion", precio=650000)
        Tarifa.objects.create(tipo_servicio="velatorio_sepultacion", precio=890000)


class BuscadorTests(Precios):
    def setUp(self):
        super().setUp()
        self.a = self.solicitud(fallecido="Ana Rojas Pino", contacto_telefono="+56 9 5555 0142", feretro=self.feretro)
        self.b = self.solicitud(fallecido="Bruno Salinas", contacto_telefono="+56 9 5555 7777", tipo_servicio="velatorio_sepultacion",
                                ceremonia_inicio=H(24 * 10), responsable=usuario("recep2", "Recepcionista"))
        self.entregar_todo(self.b)

    def codigos(self, **params):
        return [s.codigo for s in services.buscar_solicitudes(params)]

    def test_filtros(self):
        self.assertEqual(set(self.codigos()), {self.a.codigo, self.b.codigo})
        self.assertEqual(self.codigos(q="rojas"), [self.a.codigo])
        self.assertEqual(self.codigos(q=self.b.codigo.lower()), [self.b.codigo])
        self.assertEqual(self.codigos(q="0142"), [self.a.codigo])  # por los 4 últimos dígitos del teléfono
        self.assertEqual(self.codigos(servicio="velatorio_sepultacion"), [self.b.codigo])
        self.assertEqual(self.codigos(estado="en_curso"), [self.b.codigo])
        self.assertEqual(self.codigos(responsable=str(self.b.responsable_id)), [self.b.codigo])
        desde = timezone.localtime(H(24 * 8)).date().isoformat()
        self.assertEqual(self.codigos(desde=desde), [self.b.codigo])
        self.assertEqual(self.codigos(hasta=desde), [self.a.codigo])
        self.assertEqual(self.codigos(q="no existe"), [])
        self.assertEqual(set(self.codigos(servicio="invalido", desde="no-es-fecha")), {self.a.codigo, self.b.codigo})

    def test_precios_anotados(self):
        s = {x.codigo: x for x in services.buscar_solicitudes({})}[self.a.codigo]
        self.assertEqual((s.precio_servicio, s.precio_feretro, s.total_referencial), (650000, 520000, 1170000))

    def test_lista_muestra_filtros_y_detalle(self):
        self.client.force_login(self.recep)
        r = self.client.get(reverse("solicitud_list"), {"q": "Rojas"})
        self.assertContains(r, "Ana Rojas Pino")
        self.assertNotContains(r, "Bruno Salinas")
        self.assertContains(r, "$1.170.000")
        self.assertContains(r, "Descargar Excel")

    def test_excel_respeta_los_filtros(self):
        self.client.force_login(self.recep)
        r = self.client.get(reverse("solicitud_export"), {"q": "Rojas"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("attachment", r["Content-Disposition"])
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Fallecidos y servicios", "Precios"])
        ws = wb["Fallecidos y servicios"]
        filas = [[c.value for c in f] for f in ws.iter_rows(min_row=2)]
        datos = [f for f in filas if f[0] and str(f[0]).startswith("CR-")]
        self.assertEqual(len(datos), 1)
        cabecera = [c.value for c in ws[1]]
        fila = dict(zip(cabecera, datos[0]))
        self.assertEqual(fila["Fallecido/a"], "Ana Rojas Pino")
        self.assertEqual(fila["Responsable (recepción)"], "Recep Prueba")
        self.assertEqual(fila["Gama"], "Clásico")
        self.assertEqual(fila["Precio del féretro"], 520000)
        precios = [[c.value for c in f] for f in wb["Precios"].iter_rows(min_row=2)]
        self.assertIn(["Féretro", "Féretro básico", "Básico", 280000, 3], precios)
        self.assertTrue(Actividad.objects.filter(accion__startswith="Descarga Excel").exists())

    def test_excel_solo_recepcion_y_admin(self):
        for u, esperado in ((self.recep, 200), (self.admin, 200), (self.bodega, 403), (self.chofer, 403)):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("solicitud_export")).status_code, esperado, u.username)


class CalendarioTests(Precios):
    def test_muestra_ceremonia_velatorio_y_traslado(self):
        s = self.solicitud(fallecido="Eva Calendario", ceremonia_inicio=H(30), sala=self.sala1, velatorio_inicio=H(2), velatorio_fin=H(28))
        self.traslado(s=s, inicio=H(6))
        mes = timezone.localtime(H(6)).strftime("%Y-%m")
        self.client.force_login(self.recep)
        r = self.client.get(reverse("calendario"), {"mes": mes})
        self.assertContains(r, "Eva Calendario")
        self.assertContains(r, "Velatorio")
        self.assertContains(r, "Traslado")
        r = self.client.get(reverse("calendario"), {"mes": mes, "tipo": "traslado"})
        self.assertNotContains(r, "Eva Calendario")

    def test_mes_invalido_no_rompe(self):
        self.client.force_login(self.recep)
        self.assertEqual(self.client.get(reverse("calendario"), {"mes": "xx-99"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("calendario"), {"mes": "2026-13"}).status_code, 200)

    def test_chofer_solo_ve_sus_traslados(self):
        propio = self.traslado(inicio=H(5))
        ajeno = self.traslado(inicio=H(5), vehiculo=self.furgon, chofer=self.chofer2)
        self.client.force_login(self.chofer)
        mes = timezone.localtime(H(5)).strftime("%Y-%m")
        r = self.client.get(reverse("calendario"), {"mes": mes, "tipo": "todo"})
        self.assertContains(r, propio.codigo)
        self.assertNotContains(r, ajeno.codigo)
        self.assertNotContains(r, "Ceremonia ·")

    def test_bodega_no_entra(self):
        self.client.force_login(self.bodega)
        self.assertEqual(self.client.get(reverse("calendario")).status_code, 403)


class RutTests(TestCase):
    def test_rut(self):
        validar_rut("12.345.678-5")
        validar_rut("76543210-3")
        for malo in ("12.345.678-9", "abc", "1-9", ""):
            with self.assertRaises(ValidationError):
                validar_rut(malo)


class VentaTests(Precios):
    def datos(self, **kw):
        d = {"tipo_documento": "boleta", "cliente_nombre": "Cliente Uno", "cliente_rut": "", "cliente_giro": "", "cliente_direccion": "",
             "forma_pago": "efectivo"}
        d.update(kw)
        return d

    def test_boleta_calcula_neto_iva_total(self):
        s = self.solicitud()
        v = services.crear_venta(s, self.recep, self.datos(), [("Cremación", 1, 650000, None), ("Féretro básico", 2, 280000, self.basico)])
        self.assertEqual(v.total, 1210000)
        self.assertEqual((v.neto, v.iva), (1016807, 193193))
        self.assertEqual(v.neto + v.iva, v.total)
        self.assertEqual(v.numero, f"B-{v.pk:06d}")
        self.assertEqual(v.items.count(), 2)
        self.assertTrue(Actividad.objects.filter(usuario=self.recep, accion__contains="emitida").exists())

    def test_factura_exige_datos_de_empresa(self):
        s = self.solicitud()
        with self.assertRaises(ValidationError):
            services.crear_venta(s, self.recep, self.datos(tipo_documento="factura", cliente_rut="76543210-3"), [("X", 1, 1000, None)])
        v = services.crear_venta(s, self.recep, self.datos(tipo_documento="factura", cliente_nombre="Empresa SpA", cliente_rut="76.543.210-3",
                                                           cliente_giro="Seguros", cliente_direccion="Av. 1"), [("X", 1, 1000, None)])
        self.assertTrue(v.numero.startswith("F-"))
        self.assertEqual(v.cliente_rut, "76543210-3")

    def test_validaciones(self):
        s = self.solicitud()
        for lineas in ([], [("X", 0, 1000, None)], [("X", 1, 0, None)]):
            with self.assertRaises(ValidationError):
                services.crear_venta(s, self.recep, self.datos(), lineas)
        with self.assertRaises(ValidationError):
            services.crear_venta(s, self.recep, self.datos(cliente_rut="12.345.678-9"), [("X", 1, 1000, None)])
        self.assertEqual(Venta.objects.count(), 0)

    def test_la_venta_no_toca_el_stock(self):
        s = self.solicitud()
        services.crear_venta(s, self.recep, self.datos(), [("Féretro", 1, 520000, self.feretro)])
        self.feretro.refresh_from_db()
        self.assertEqual(self.feretro.stock, 5)

    def test_anular_solo_una_vez(self):
        v = services.crear_venta(self.solicitud(), self.recep, self.datos(), [("X", 1, 1000, None)])
        services.anular_venta(v, self.admin)
        v.refresh_from_db()
        self.assertTrue(v.anulada)
        with self.assertRaises(ValidationError):
            services.anular_venta(v, self.admin)

    def test_flujo_completo_por_pantalla(self):
        s = self.solicitud(feretro=self.basico)
        self.client.force_login(self.recep)
        r = self.client.get(reverse("venta_create", args=[s.pk]))
        self.assertContains(r, "Féretro básico")  # fila precargada con el féretro elegido
        self.assertContains(r, "Cremación")       # y con el servicio
        r = self.client.post(reverse("venta_create", args=[s.pk]), {
            **self.datos(), "form-TOTAL_FORMS": 2, "form-INITIAL_FORMS": 0, "form-MIN_NUM_FORMS": 1, "form-MAX_NUM_FORMS": 1000,
            "form-0-item": "s:cremacion", "form-0-cantidad": 1, "form-1-item": f"a:{self.basico.pk}", "form-1-cantidad": 1})
        v = Venta.objects.get()
        self.assertRedirects(r, reverse("venta_detail", args=[v.pk]))
        self.assertEqual(v.total, 930000)
        self.assertContains(self.client.get(reverse("venta_detail", args=[v.pk])), "$930.000")
        pdf = self.client.get(reverse("venta_pdf", args=[v.pk]))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_precio_se_toma_del_catalogo_no_del_formulario(self):
        s = self.solicitud()
        self.client.force_login(self.recep)
        self.client.post(reverse("venta_create", args=[s.pk]), {
            **self.datos(), "form-TOTAL_FORMS": 1, "form-INITIAL_FORMS": 0, "form-MIN_NUM_FORMS": 1, "form-MAX_NUM_FORMS": 1000,
            "form-0-item": "s:cremacion", "form-0-cantidad": 1, "form-0-precio": 1})
        self.assertEqual(Venta.objects.get().total, 650000)

    def test_producto_inexistente_se_rechaza(self):
        s = self.solicitud()
        self.client.force_login(self.recep)
        self.client.post(reverse("venta_create", args=[s.pk]), {
            **self.datos(), "form-TOTAL_FORMS": 1, "form-INITIAL_FORMS": 0, "form-MIN_NUM_FORMS": 1, "form-MAX_NUM_FORMS": 1000,
            "form-0-item": "a:99999", "form-0-cantidad": 1})
        self.assertEqual(Venta.objects.count(), 0)

    def test_permisos_de_ventas(self):
        s = self.solicitud()
        v = services.crear_venta(s, self.recep, self.datos(), [("X", 1, 1000, None)])
        rutas = [reverse("venta_list"), reverse("venta_create", args=[s.pk]), reverse("venta_detail", args=[v.pk]), reverse("venta_pdf", args=[v.pk])]
        for u, esperado in ((self.recep, 200), (self.admin, 200), (self.bodega, 403), (self.chofer, 403)):
            self.client.force_login(u)
            for ruta in rutas:
                self.assertEqual(self.client.get(ruta).status_code, esperado, f"{ruta} {u.username}")

    def test_solo_admin_anula(self):
        v = services.crear_venta(self.solicitud(), self.recep, self.datos(), [("X", 1, 1000, None)])
        self.client.force_login(self.recep)
        self.assertEqual(self.client.post(reverse("venta_anular", args=[v.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("venta_anular", args=[v.pk]))
        v.refresh_from_db()
        self.assertTrue(v.anulada)
        self.assertEqual(Solicitud.objects.get(pk=v.solicitud_id).ventas.count(), 1)

    def test_venta_anulada_no_suma_en_facturado(self):
        s = self.solicitud()
        v = services.crear_venta(s, self.recep, self.datos(), [("X", 1, 1000, None)])
        services.crear_venta(s, self.recep, self.datos(), [("Y", 1, 2000, None)])
        services.anular_venta(v, self.admin)
        self.assertEqual(services.buscar_solicitudes({"q": s.codigo})[0].facturado, 2000)

    def test_api_solo_lectura(self):
        s = self.solicitud()
        v = services.crear_venta(s, self.recep, self.datos(), [("X", 1, 1000, None)])
        c = APIClient()
        c.force_authenticate(self.recep)
        self.assertEqual(c.get("/api/ventas/").json()["results"][0]["total"], 1000)
        self.assertIn(c.post("/api/ventas/", {}, format="json").status_code, (403, 405))
        c.force_authenticate(self.bodega)
        self.assertEqual(c.get("/api/ventas/").status_code, 403)
        self.assertEqual(c.get("/api/tarifas/").status_code, 200)


class PreciosVistaTests(Precios):
    def test_pagina_de_precios(self):
        for u, esperado in ((self.recep, 200), (self.bodega, 200), (self.admin, 200), (self.chofer, 403)):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("precios")).status_code, esperado, u.username)
        self.client.force_login(self.recep)
        r = self.client.get(reverse("precios"))
        for texto in ("Básico", "Clásico", "$280.000", "$520.000", "$650.000"):
            self.assertContains(r, texto)

    def test_solo_admin_edita_tarifas(self):
        self.client.force_login(self.recep)
        self.assertEqual(self.client.get(reverse("tarifas_editar")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tarifas_editar")).status_code, 200)

    def test_feretro_necesita_gama(self):
        self.client.force_login(self.bodega)
        r = self.client.post(reverse("articulo_create"), {"nombre": "Féretro nuevo", "categoria": "feretros", "gama": "", "precio": 100000,
                                                         "unidad": "unidades", "stock": 1, "stock_minimo": 1})
        self.assertContains(r, "básico, clásico o premium")
        self.assertFalse(Articulo.objects.filter(nombre="Féretro nuevo").exists())


# --------------------------------------------------------------------------
# Landing pública y gráficos del panel
# --------------------------------------------------------------------------
class LandingTests(Precios):
    def setUp(self):
        super().setUp()
        Articulo.objects.filter(pk=self.feretro.pk).update(descripcion="Madera sólida barnizada")
        self.urna = Articulo.objects.create(nombre="Urna de madera", categoria="urnas", precio=145000, stock=2, stock_minimo=1,
                                            descripcion="Urna con placa")
        self.oculto = Articulo.objects.create(nombre="Féretro oculto", categoria="feretros", gama="premium", precio=999000, publicado=False)
        self.sin_precio = Articulo.objects.create(nombre="Urna sin precio", categoria="urnas", precio=0)

    def test_publica_sin_iniciar_sesion(self):
        for ruta in ("/", reverse("catalogo")):
            r = self.client.get(ruta)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Féretro básico")
            self.assertContains(r, "$280.000")
            self.assertContains(r, "Urna de madera")
            self.assertContains(r, "$145.000")
            self.assertContains(r, "Ingreso del personal")

    def test_no_muestra_ocultos_ni_sin_precio_ni_stock(self):
        r = self.client.get("/")
        self.assertNotContains(r, "Féretro oculto")
        self.assertNotContains(r, "Urna sin precio")
        self.assertNotContains(r, "stock", html=False)
        self.assertNotContains(r, "Persona Ficticia")

    def test_logueado_ve_su_panel_en_la_raiz(self):
        self.client.force_login(self.recep)
        r = self.client.get("/")
        self.assertContains(r, "Mi panel")
        self.assertNotContains(r, "Ingreso del personal")

    def test_bodega_publica_o_oculta_desde_el_formulario(self):
        self.client.force_login(self.bodega)
        self.client.post(reverse("articulo_edit", args=[self.urna.pk]), {
            "nombre": "Urna de madera", "categoria": "urnas", "gama": "", "precio": 150000, "descripcion": "Nueva", "unidad": "unidades", "stock_minimo": 1})
        self.urna.refresh_from_db()
        self.assertFalse(self.urna.publicado)  # el checkbox desmarcado la oculta
        self.client.logout()
        self.assertNotContains(self.client.get("/"), "Urna de madera")


class GraficosTests(Precios):
    def test_paneles_con_graficos_por_rol(self):
        s = self.solicitud(ceremonia_inicio=H(30))
        services.crear_venta(s, self.recep, {"tipo_documento": "boleta", "cliente_nombre": "C", "forma_pago": "efectivo"}, [("X", 1, 119000, None)])
        esperado = {"admin": ["Solicitudes por estado", "Ventas por mes", "Servicios más solicitados", "Ceremonias en los próximos"],
                    "recep": ["Mis solicitudes por etapa", "Ceremonias en los próximos"],
                    "bodega": ["Stock frente al mínimo", "Salidas de bodega"],
                    "chofer1": ["Traslados completados"]}
        usuarios = {"admin": self.admin, "recep": self.recep, "bodega": self.bodega, "chofer1": self.chofer}
        for nombre, textos in esperado.items():
            self.client.force_login(usuarios[nombre])
            r = self.client.get("/")
            self.assertEqual(r.status_code, 200, nombre)
            for texto in textos:
                self.assertContains(r, texto, msg_prefix=nombre)

    def test_datos_de_los_graficos(self):
        from . import graficos
        a = self.solicitud(ceremonia_inicio=H(30))
        self.solicitud(ceremonia_inicio=H(72))
        self.entregar_todo(a)
        todas = list(Solicitud.objects.prefetch_related("documentos"))
        g = {f["etiqueta"]: f["valor"] for f in graficos.por_estado(todas)["filas"]}
        self.assertEqual((g["En curso"], g["Documentos pendientes"]), (1, 1))
        services.crear_venta(a, self.recep, {"tipo_documento": "boleta", "cliente_nombre": "C", "forma_pago": "efectivo"}, [("X", 1, 119000, None)])
        v = graficos.ingresos_mensuales()
        self.assertEqual(len(v["filas"]), 6)
        self.assertEqual(v["filas"][-1]["valor"], 119000)
        self.assertEqual(v["filas"][-1]["texto"], "$119.000")
        # una venta anulada no cuenta
        venta = Venta.objects.get()
        services.anular_venta(venta, self.admin)
        self.assertEqual(graficos.ingresos_mensuales()["filas"][-1]["valor"], 0)
        st = graficos.stock_vs_minimo()
        self.assertTrue(all(0 <= f["pct"] <= 100 and 0 <= f["marca"] <= 100 for f in st["filas"]))

    def test_sin_datos_no_rompe(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/"), "Todavía no hay datos")


# --------------------------------------------------------------------------
# Imágenes, ausencias, tema verde y AJAX
# --------------------------------------------------------------------------
import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from .models import Ausencia

MEDIA_TMP = tempfile.mkdtemp(prefix="cr_media_")


def imagen_png(nombre="foto.png", tam=(40, 30), color=(30, 100, 70)):
    buf = BytesIO()
    Image.new("RGB", tam, color).save(buf, "PNG")
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ImagenesTests(Precios):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)

    def datos(self, **kw):
        d = {"nombre": "Féretro básico", "categoria": "feretros", "gama": "basico", "precio": 280000, "descripcion": "Sobrio", "publicado": "on",
             "unidad": "unidades", "stock_minimo": 1}
        d.update(kw)
        return d

    def test_bodega_sube_foto_y_se_ve_en_el_sitio_publico(self):
        self.client.force_login(self.bodega)
        r = self.client.post(reverse("articulo_edit", args=[self.basico.pk]), {**self.datos(), "imagen": imagen_png()})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.basico.refresh_from_db()
        self.assertTrue(self.basico.imagen.name.startswith("catalogo/"))
        self.assertRegex(self.basico.imagen.name, r"^catalogo/[0-9a-f]{32}\.png$")  # nombre aleatorio
        self.client.logout()
        r = self.client.get("/")
        self.assertContains(r, self.basico.imagen.url)
        self.assertContains(r, f'alt="{self.basico.nombre}"')

    def test_sin_foto_se_muestra_la_ilustracion(self):
        r = self.client.get("/")
        self.assertContains(r, "Ilustración de")

    def test_rechaza_archivos_que_no_son_imagen(self):
        self.client.force_login(self.bodega)
        falso = SimpleUploadedFile("virus.png", b"no soy una imagen", content_type="image/png")
        r = self.client.post(reverse("articulo_edit", args=[self.basico.pk]), {**self.datos(), "imagen": falso})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["form"].errors["imagen"])
        svg = SimpleUploadedFile("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml")
        r = self.client.post(reverse("articulo_edit", args=[self.basico.pk]), {**self.datos(), "imagen": svg})
        self.assertTrue(r.context["form"].errors["imagen"])
        self.basico.refresh_from_db()
        self.assertFalse(self.basico.imagen)

    def test_rechaza_imagen_muy_pesada(self):
        self.client.force_login(self.bodega)
        grande = SimpleUploadedFile("grande.png", imagen_png().read() + b"0" * (3 * 1024 * 1024 + 1), content_type="image/png")
        r = self.client.post(reverse("articulo_edit", args=[self.basico.pk]), {**self.datos(), "imagen": grande})
        self.assertIn("3 MB", " ".join(r.context["form"].errors.get("imagen", [])))

    def test_formulario_es_multipart(self):
        self.client.force_login(self.bodega)
        self.assertContains(self.client.get(reverse("articulo_edit", args=[self.basico.pk])), 'enctype="multipart/form-data"')

    def test_selects_en_blanco_en_espanol(self):
        self.client.force_login(self.bodega)
        r = self.client.get(reverse("articulo_create"))
        self.assertNotContains(r, "Select an option")
        self.assertNotContains(r, "---------")
        self.assertContains(r, "Elige una opción")

    def test_recepcion_no_sube_fotos(self):
        self.client.force_login(self.recep)
        self.assertEqual(self.client.post(reverse("articulo_edit", args=[self.basico.pk]), {**self.datos(), "imagen": imagen_png()}).status_code, 403)


class AusenciaTests(Base):
    def dia(self, n):
        return timezone.localdate() + timedelta(days=n)

    def pedir(self, u, desde=3, hasta=4, **kw):
        datos = {"tipo": "permiso", "desde": self.dia(desde), "hasta": self.dia(hasta), "motivo": "Trámite"}
        datos.update(kw)
        return services.solicitar_ausencia(u, datos)

    def test_solicitud_queda_pendiente_y_admin_la_aprueba(self):
        a = self.pedir(self.chofer)
        self.assertEqual((a.estado, a.dias), ("pendiente", 2))
        a = services.resolver_ausencia(a, "aprobar", self.admin, "Ok")
        self.assertEqual((a.estado, a.resuelta_por, a.comentario), ("aprobada", self.admin, "Ok"))
        with self.assertRaises(ValidationError):
            services.resolver_ausencia(a, "rechazar", self.admin)  # ya resuelta

    def test_rechazar(self):
        a = services.resolver_ausencia(self.pedir(self.recep), "rechazar", self.admin, "Hay mucho trabajo")
        self.assertEqual(a.estado, "rechazada")

    def test_fechas_invalidas(self):
        with self.assertRaises(ValidationError):
            self.pedir(self.recep, desde=5, hasta=3)
        with self.assertRaises(ValidationError):
            self.pedir(self.recep, desde=-2, hasta=1)          # personal: no fechas pasadas
        with self.assertRaises(ValidationError):
            self.pedir(self.recep, desde=1, hasta=90)           # más de 60 días
        services.solicitar_ausencia(self.recep, {"tipo": "licencia", "desde": self.dia(-3), "hasta": self.dia(-1), "motivo": ""}, por=self.admin)  # admin sí

    def test_no_se_solapan_las_de_una_misma_persona_pero_si_las_rechazadas(self):
        a = self.pedir(self.chofer, 3, 5)
        with self.assertRaises(ValidationError):
            self.pedir(self.chofer, 4, 6)
        self.pedir(self.bodega, 4, 6)  # otra persona: sin problema
        services.resolver_ausencia(a, "rechazar", self.admin)
        self.pedir(self.chofer, 4, 6)  # la rechazada ya no bloquea

    def test_admin_registra_y_queda_aprobada(self):
        a = services.solicitar_ausencia(self.bodega, {"tipo": "vacaciones", "desde": self.dia(1), "hasta": self.dia(3), "motivo": ""}, por=self.admin)
        self.assertEqual(a.estado, "aprobada")

    def test_no_se_aprueba_si_el_chofer_tiene_traslados(self):
        t = self.traslado(inicio=H(24 * 3 + 2))
        a = self.pedir(self.chofer, 3, 3)
        with self.assertRaises(ValidationError) as e:
            services.resolver_ausencia(a, "aprobar", self.admin)
        self.assertIn(t.codigo, " ".join(e.exception.messages))
        a.refresh_from_db()
        self.assertEqual(a.estado, "pendiente")

    def test_chofer_ausente_no_puede_recibir_traslados(self):
        a = self.pedir(self.chofer, 2, 4)
        services.resolver_ausencia(a, "aprobar", self.admin)
        with self.assertRaises(ValidationError) as e:
            self.traslado(inicio=H(24 * 3))
        self.assertIn("ausencia aprobada", " ".join(e.exception.messages))
        self.traslado(inicio=H(24 * 3), chofer=self.chofer2, vehiculo=self.furgon)   # otro chofer sí
        self.traslado(inicio=H(24 * 8))                                              # fuera de la ausencia, sí

    def test_cancelar(self):
        a = self.pedir(self.chofer)
        with self.assertRaises(ValidationError):
            services.cancelar_ausencia(a, self.recep)         # no es suya
        services.cancelar_ausencia(a, self.chofer)
        self.assertFalse(Ausencia.objects.exists())
        b = self.pedir(self.chofer)
        services.resolver_ausencia(b, "aprobar", self.admin)
        with self.assertRaises(ValidationError):
            services.cancelar_ausencia(b, self.chofer)        # ya resuelta
        services.cancelar_ausencia(b, self.admin)             # el admin sí puede

    def test_ausentes_hoy(self):
        services.solicitar_ausencia(self.recep, {"tipo": "permiso", "desde": self.dia(0), "hasta": self.dia(0), "motivo": ""}, por=self.admin)
        self.pedir(self.bodega, 0, 0)  # pendiente: no cuenta
        self.assertEqual([a.usuario for a in services.ausentes_en(self.dia(0))], [self.recep])

    # ---- pantallas ----
    def test_cada_rol_pide_la_suya_por_pantalla(self):
        self.client.force_login(self.chofer)
        r = self.client.post(reverse("ausencia_mias"), {"tipo": "permiso", "desde": self.dia(2).isoformat(), "hasta": self.dia(2).isoformat(), "motivo": "Médico"})
        self.assertRedirects(r, reverse("ausencia_mias"))
        self.assertEqual(Ausencia.objects.get().usuario, self.chofer)
        self.assertContains(self.client.get(reverse("ausencia_mias")), "Pendiente")

    def test_error_de_fechas_se_muestra_en_el_formulario(self):
        self.client.force_login(self.recep)
        r = self.client.post(reverse("ausencia_mias"), {"tipo": "permiso", "desde": self.dia(5).isoformat(), "hasta": self.dia(2).isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no puede ser anterior")
        self.assertEqual(Ausencia.objects.count(), 0)

    def test_permisos_de_pantallas(self):
        a = self.pedir(self.chofer)
        usuarios = {"admin": self.admin, "recep": self.recep, "bodega": self.bodega, "chofer1": self.chofer}
        esperado = {"ausencia_list": {"admin"}, "ausencia_create": {"admin"}, "ausencia_mias": {"admin", "recep", "bodega", "chofer1"}}
        for nombre, ok in esperado.items():
            for n, u in usuarios.items():
                self.client.force_login(u)
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200 if n in ok else 403, f"{nombre} {n}")
        self.client.force_login(self.recep)
        self.assertEqual(self.client.post(reverse("ausencia_resolver", args=[a.pk]), {"accion": "aprobar"}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("ausencia_resolver", args=[a.pk]), {"accion": "aprobar", "comentario": "Ok"})
        a.refresh_from_db()
        self.assertEqual(a.estado, "aprobada")

    def test_panel_del_admin_muestra_ausencias(self):
        self.pedir(self.chofer)
        services.solicitar_ausencia(self.recep, {"tipo": "permiso", "desde": self.dia(0), "hasta": self.dia(0), "motivo": ""}, por=self.admin)
        self.client.force_login(self.admin)
        r = self.client.get("/")
        self.assertContains(r, "Ausencias del personal")
        self.assertContains(r, "Personas ausentes hoy")
        self.assertContains(r, "Aprobar")

    def test_calendario_muestra_ausencias_a_recepcion_pero_no_al_chofer(self):
        d = self.dia(1)
        services.solicitar_ausencia(self.bodega, {"tipo": "vacaciones", "desde": d, "hasta": d, "motivo": ""}, por=self.admin)
        mes = d.strftime("%Y-%m")
        self.client.force_login(self.recep)
        self.assertContains(self.client.get(reverse("calendario"), {"mes": mes}), "Bodega Prueba")
        self.client.force_login(self.chofer)
        self.assertNotContains(self.client.get(reverse("calendario"), {"mes": mes}), "Bodega Prueba")

    def test_api(self):
        c = APIClient()
        c.force_authenticate(self.chofer)
        r = c.post("/api/ausencias/", {"tipo": "permiso", "desde": self.dia(2).isoformat(), "hasta": self.dia(3).isoformat(), "motivo": "x"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()["estado"], r.json()["dias"]), ("pendiente", 2))
        pk = r.json()["id"]
        # el chofer no se auto-aprueba ni ve las de otros
        self.assertEqual(c.post(f"/api/ausencias/{pk}/resolver/", {"accion": "aprobar"}, format="json").status_code, 403)
        self.pedir(self.bodega, 6, 7)
        self.assertEqual(len(c.get("/api/ausencias/").json()["results"]), 1)
        c.force_authenticate(self.admin)
        self.assertEqual(len(c.get("/api/ausencias/").json()["results"]), 2)
        self.assertEqual(c.post(f"/api/ausencias/{pk}/resolver/", {"accion": "aprobar"}, format="json").json()["estado"], "aprobada")
        self.assertEqual(c.post("/api/ausencias/", {"tipo": "permiso", "desde": "2020-01-01", "hasta": "2020-01-02"}, format="json").status_code, 201)  # admin sí puede


class TemaYAjaxTests(Base):
    def test_tema_verde_y_sin_granate(self):
        css = open(settings_css()).read()
        self.assertIn("--brand:#a8dcc1", css)
        self.assertNotIn("#7a1f2b", css.lower())

    def test_pdf_y_excel_usan_el_verde(self):
        from . import documentos_pdf, exportacion
        self.assertEqual(exportacion.VERDE, "A8DCC1")
        self.assertTrue(hasattr(documentos_pdf, "VERDE"))

    def test_base_carga_el_script_y_tiene_zona_de_anuncios(self):
        self.client.force_login(self.recep)
        r = self.client.get("/")
        self.assertContains(r, "js/cristorey.js")
        self.assertContains(r, 'id="cr-live"')
        self.assertContains(r, 'id="main"')

    def test_no_quedan_scripts_ni_manejadores_en_linea(self):
        import pathlib
        for f in pathlib.Path("templates").rglob("*.html"):
            texto = f.read_text()
            for prohibido in ("onclick=", "onsubmit=", "onchange=", "<script>"):
                self.assertNotIn(prohibido, texto, f"{f}: {prohibido}")

    def test_respuesta_normal_y_ajax_son_la_misma_pagina(self):
        """El AJAX pide la página completa y reemplaza el contenido, así que sirve igual con y sin el encabezado."""
        self.client.force_login(self.recep)
        a = self.client.get(reverse("solicitud_list"))
        b = self.client.get(reverse("solicitud_list"), HTTP_X_REQUESTED_WITH="fetch")
        import re
        sin_token = lambda r: re.sub(rb'name="csrfmiddlewaretoken" value="[^"]+"', b"", r.content)
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(sin_token(a), sin_token(b))


def settings_css():
    import pathlib
    return str(pathlib.Path("static/css/cristorey.css"))


# --------------------------------------------------------------------------
# Abonos, Webpay, perfiles, etiquetas, medidas y duración de servicios
# --------------------------------------------------------------------------
from unittest import mock  # noqa: E402

from .models import Abono, Etiqueta, Perfil  # noqa: E402


class AbonosTests(Precios):
    def setUp(self):
        super().setUp()
        self.s = self.solicitud(feretro=self.feretro)  # 650.000 + 520.000 de referencia, ver saldo

    def saldo(self):
        return services._saldo(self.s)[2]

    def test_abono_manual_baja_el_saldo(self):
        antes = self.saldo()
        services.registrar_abono(self.s, self.recep, 100000, "efectivo")
        self.assertEqual(self.saldo(), antes - 100000)

    def test_abono_no_puede_superar_el_saldo_ni_ser_menor_al_minimo(self):
        with self.assertRaises(ValidationError):
            services.registrar_abono(self.s, self.recep, self.saldo() + 1, "efectivo")
        with self.assertRaises(ValidationError):
            services.registrar_abono(self.s, self.recep, 500, "efectivo")

    def test_webpay_no_se_registra_como_manual(self):
        with self.assertRaises(ValidationError):
            services.registrar_abono(self.s, self.recep, 50000, "webpay")

    def test_anular_devuelve_el_saldo_y_solo_una_vez(self):
        antes = self.saldo()
        ab = services.registrar_abono(self.s, self.recep, 80000, "transferencia")
        services.anular_abono(ab, self.admin)
        self.assertEqual(self.saldo(), antes)
        with self.assertRaises(ValidationError):
            services.anular_abono(ab, self.admin)

    def test_solo_admin_anula(self):
        ab = services.registrar_abono(self.s, self.recep, 80000, "efectivo")
        self.client.force_login(self.recep)
        self.assertEqual(self.client.post(reverse("abono_anular", args=[ab.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("abono_anular", args=[ab.pk]))
        ab.refresh_from_db()
        self.assertEqual(ab.estado, "anulado")

    def test_permisos_del_abono(self):
        url = reverse("abono_create", args=[self.s.pk])
        for u, esperado in ((self.recep, 302), (self.admin, 302), (self.bodega, 403), (self.chofer, 403)):
            self.client.force_login(u)
            self.assertEqual(self.client.post(url, {"monto": 1000, "medio": "efectivo"}).status_code, esperado, u.username)


class WebpayTests(Precios):
    def setUp(self):
        super().setUp()
        self.s = self.solicitud(feretro=self.feretro)

    def iniciar(self, monto=100000):
        with mock.patch("servicios.services.webpay.crear", return_value=("https://webpay.test/pagar", "tok123")):
            return services.iniciar_webpay(self.s, self.recep, monto, "http://testserver/pagos/webpay/retorno/")

    def test_pago_aprobado_confirma_el_abono(self):
        self.iniciar()
        r = {"status": "AUTHORIZED", "response_code": 0, "amount": 100000, "authorization_code": "1213", "card_detail": {"card_number": "6623"}}
        with mock.patch("servicios.services.webpay.confirmar", return_value=(True, r)):
            ab = services.cerrar_webpay("tok123")
        self.assertEqual((ab.estado, ab.tarjeta_ultimos4, ab.codigo_autorizacion), ("confirmado", "6623", "1213"))

    def test_monto_distinto_se_rechaza(self):
        self.iniciar()
        r = {"status": "AUTHORIZED", "response_code": 0, "amount": 5000}
        with mock.patch("servicios.services.webpay.confirmar", return_value=(True, r)):
            self.assertEqual(services.cerrar_webpay("tok123").estado, "rechazado")

    def test_cancelado_y_idempotente(self):
        self.iniciar()
        self.assertEqual(services.cerrar_webpay("tok123", cancelado=True).estado, "rechazado")
        with mock.patch("servicios.services.webpay.confirmar") as c:
            services.cerrar_webpay("tok123")
            c.assert_not_called()

    def test_sin_conexion_queda_pendiente(self):
        self.iniciar()
        with mock.patch("servicios.services.webpay.confirmar", side_effect=RuntimeError("sin red")):
            self.assertEqual(services.cerrar_webpay("tok123").estado, "pendiente")

    def test_si_transbank_falla_al_crear_no_queda_pendiente(self):
        with mock.patch("servicios.services.webpay.crear", side_effect=RuntimeError("sin red")):
            with self.assertRaises(ValidationError):
                services.iniciar_webpay(self.s, self.recep, 100000, "http://x/")
        self.assertFalse(Abono.objects.filter(estado="pendiente").exists())

    def test_pendiente_no_descuenta_saldo_y_token_desconocido(self):
        antes = services._saldo(self.s)[2]
        self.iniciar()
        self.assertEqual(services._saldo(self.s)[2], antes)
        self.assertIsNone(services.cerrar_webpay("no-existe"))
        self.assertEqual(self.client.get(reverse("webpay_retorno"), {"token_ws": "no-existe"}).status_code, 302)

    def test_solo_recepcion_inicia_el_pago(self):
        for u in (self.bodega, self.chofer):
            self.client.force_login(u)
            self.assertEqual(self.client.post(reverse("webpay_iniciar", args=[self.s.pk]), {"monto": 5000}).status_code, 403)


class PerfilEtiquetaMedidasTests(Precios):
    def test_cada_persona_edita_su_perfil(self):
        self.client.force_login(self.chofer)
        self.assertEqual(self.client.get(reverse("perfil")).status_code, 200)
        r = self.client.post(reverse("perfil"), {"first_name": "Luis", "last_name": "Maldonado", "email": "l@example.com",
                                                 "telefono": "+56 9 5555 1234", "cargo": "Chofer", "rut": "", "direccion": "", "contacto_emergencia": ""})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Perfil.objects.get(usuario=self.chofer).cargo, "Chofer")

    def test_solo_admin_edita_perfiles_ajenos(self):
        url = reverse("usuario_edit", args=[self.chofer.pk])
        self.client.force_login(self.recep)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_rut_invalido_en_perfil(self):
        with self.assertRaises(ValidationError):
            Perfil(usuario=self.chofer, rut="11.111.111-2").full_clean()

    def test_etiquetas_solo_recepcion_y_admin(self):
        for u, esperado in ((self.recep, 200), (self.admin, 200), (self.bodega, 403), (self.chofer, 403)):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("etiqueta_list")).status_code, esperado, u.username)

    def test_borrar_etiqueta_la_quita_de_las_solicitudes(self):
        e = Etiqueta.objects.create(nombre="Convenio", color="ok")
        s = self.solicitud()
        s.etiquetas.add(e)
        self.client.force_login(self.admin)
        self.client.post(reverse("etiqueta_delete", args=[e.pk]))
        self.assertEqual(s.etiquetas.count(), 0)

    def test_medidas_validadas_y_visibles_en_el_sitio_publico(self):
        a = Articulo.objects.create(nombre="Urna roble", categoria="urnas", precio=90000, stock=2, alto_cm=25, ancho_cm=18, largo_cm=18, material="Roble")
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Urna roble")
        self.assertContains(r, "25")
        a.alto_cm = 0
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_duracion_de_tarifa_limitada(self):
        t = Tarifa(tipo_servicio="traslado_cremacion", precio=1000, duracion_horas=500)
        with self.assertRaises(ValidationError):
            t.full_clean()
