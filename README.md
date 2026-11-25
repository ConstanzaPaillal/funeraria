# Cristo Rey — Sistema de gestión de servicios funerarios

Django 6.1 + Django REST Framework + PostgreSQL. Seguimiento de cada servicio funerario desde que la familia
lo solicita hasta que termina, con salas de velatorio, traslados, bodega de materiales y una vista pública
para que la familia consulte el avance.

Todos los datos de demostración son ficticios.

## Roles

| Rol | Qué hace |
|---|---|
| **Administrador** (superusuario) | Todo: usuarios y roles, flota, actividad, eliminar solicitudes, Django Admin. |
| **Recepción** (2) | Crea y gestiona solicitudes, marca documentos, avanza etapas, agenda salas y programa traslados. |
| **Bodega** (1) | Inventario, entradas y salidas de stock, pedidos de materiales por solicitud. |
| **Chofer** (2) | Ve **solo sus** traslados y los marca: iniciar, retraso, completado. |

Los roles son grupos de Django (`Recepcionista`, `Bodega`, `Chofer`); el administrador es superusuario.
La misma regla (`servicios/permissions.py`) protege las pantallas y la API. Solo el administrador crea usuarios
(menú *Usuarios y roles*).

## Calendario, buscador, precios y ventas

- **Calendario** (`/calendario/`): vista mensual con ceremonias, velatorios y traslados (filtrable por tipo). Recepción y
  administrador ven todo; cada chofer ve solo sus traslados. En el celular se muestra como agenda.
- **Buscador de solicitudes**: por persona, código, familiar o últimos 4 dígitos del teléfono, y por estado, tipo de servicio,
  responsable y rango de fechas. Cada fila se **despliega** para ver contacto, velatorio, responsable, féretro y precios.
  **Descargar Excel** entrega lo que muestra el buscador (hoja «Fallecidos y servicios» y hoja «Precios»).
- **Precios** (`/precios/`): féretros básico, clásico y premium, tarifas por tipo de servicio y otros artículos
  (valores con IVA incluido). Bodega edita los precios de artículos (Inventario); el administrador, las tarifas de servicios.
  Cada solicitud puede tener un **féretro elegido**.
- **Ventas**: desde una solicitud, *Registrar venta* (recepción y administrador). Se elige boleta o factura (la factura exige
  RUT válido, giro y dirección), los productos salen del catálogo con su precio vigente, y se calcula neto, IVA 19 % y total.
  Se puede ver, imprimir o **descargar en PDF**. Solo el administrador anula. Registrar una venta no descuenta stock:
  los materiales salen de bodega con los pedidos.
- Los datos del emisor (razón social, RUT, dirección) están en `EMPRESA` de `config/settings.py` (o variables
  `EMPRESA_RAZON_SOCIAL`, `EMPRESA_RUT`, `EMPRESA_DIRECCION`). Los documentos son **comprobantes internos**: no reemplazan
  la boleta o factura electrónica que se emite ante el SII.

## Imágenes, ausencias, tema verde y navegación sin recargar

- **Fotos de féretros y urnas**: Bodega sube la foto al editar un artículo (Inventario → Editar → *Foto*; JPG, PNG o WebP de
  hasta 3 MB; se valida que sea una imagen real). La foto aparece en el sitio público y como miniatura en el inventario; sin
  foto se muestra una ilustración. Los archivos quedan en la carpeta `media/` (en EC2 los sirve Nginx).
- **Ausencias del personal**: cada persona pide un permiso, vacaciones o licencia en *Mis ausencias*. El administrador las
  aprueba o rechaza (con comentario) desde el **panel** o desde *Ausencias*, registra ausencias de cualquier persona y ve
  quién falta hoy y en los próximos 14 días. Las aprobadas aparecen en el **calendario** (tipo *Ausencias*).
  Reglas: no se solapan las de una misma persona, no más de 60 días, el personal no pide fechas pasadas, un chofer ausente no
  recibe traslados y no se aprueba una ausencia si el chofer tiene traslados programados esos días.
- **Tema verde claro**: la paleta (verde menta suave, texto oscuro) está en las variables de `static/css/cristorey.css` (siempre claro, verde menta, con contrastes verificados).
  Los colores del PDF y del Excel están en `documentos_pdf.py` y `exportacion.py`.
- **Navegación sin recargar (AJAX)**: `static/js/cristorey.js` actualiza solo el contenido al navegar, enviar formularios,
  marcar documentos, aprobar ausencias o buscar (la búsqueda es en vivo). Conserva el scroll y el foco, funciona con los
  botones atrás y adelante, anuncia los mensajes a lectores de pantalla y, si JavaScript falla o está desactivado, todo
  sigue funcionando como páginas normales. Las descargas (Excel, PDF), el ingreso y la salida no se interceptan.

## Urnas con medidas, servicios, perfiles, etiquetas, abonos y Webpay

- **Urnas y féretros**: Bodega administra el catálogo completo en *Inventario* (crear, editar, eliminar) con medidas (alto, ancho, largo en cm),
  material, precio, descripción, foto principal y galería de fotos adicionales. Todo se muestra en el sitio público.
- **Servicios**: el administrador edita las tarifas en *Precios → Editar tarifas*: precio, qué incluye, **duración estimada** y si se ofrece.
- **Perfiles**: cada persona edita sus datos en *Mi perfil* (RUT validado, teléfono, cargo, contacto de emergencia) y cambia su clave;
  el administrador edita los perfiles y el rol de cualquiera (*Usuarios y roles*), sin poder quitarse su propio rol ni dejar al sistema sin administrador.
- **Etiquetas**: recepción y administrador crean etiquetas de color (el color siempre va con el nombre) y las asignan a las solicitudes.
- **Abonos de reserva**: desde una solicitud se registra un abono (efectivo, transferencia, tarjeta) de al menos $1.000 y nunca mayor al saldo;
  el saldo pendiente se ve en la solicitud. Solo el administrador anula.
- **Transbank Webpay Plus**: *Pagar con Webpay* lleva al cliente al pago y vuelve confirmando el abono (monto verificado, resultado idempotente,
  si Transbank no responde queda pendiente). Por defecto usa el **ambiente de pruebas** con tarjetas de prueba: no se cobra dinero real.
  Para producción define `TRANSBANK_ENV=production`, `TRANSBANK_COMMERCE_CODE` y `TRANSBANK_API_KEY` en `.env` (los entrega Transbank
  al contratar) y usa una URL pública con HTTPS, porque Webpay debe poder volver a tu sitio.

## Reglas de negocio (en `servicios/services.py`)

- Una **sala** no puede tener dos velatorios en el mismo horario.
- Un **vehículo** o un **chofer** no pueden tener dos traslados activos que se solapen; el vehículo debe estar utilizable.
- Los traslados siguen un flujo: programado → en camino → (retrasado) → completado.
- Cada solicitud tiene 3 **documentos**; no se puede avanzar a *Ceremonia* con documentos pendientes.
- El **estado** de la solicitud se calcula: *finalizada*, *en curso*, *documentos pendientes* y *urgente*
  (faltan documentos y la ceremonia es en menos de 24 h).
- El **stock** nunca queda negativo; entregar un pedido descuenta todo o nada, de forma atómica.
- **Vista de la familia** (`/familia/`): código (CR-0001) + últimos 4 dígitos del teléfono, con límite de intentos por IP.
- **Avisos por correo** a familias con documentos pendientes (máximo uno cada 24 h).
- **Registro de actividad** de lo que hace cada persona.

## Probar en tu computador

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1          # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
$env:USE_SQLITE="True"               # Linux/Mac: export USE_SQLITE=True   (sin PostgreSQL)
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Abre http://127.0.0.1:8000/ e ingresa con:

| Usuario | Clave | Rol |
|---|---|---|
| admin | Admin12345! | Administrador |
| msoto, paravena | Recepcion123! | Recepción |
| hcastillo | Bodega12345! | Bodega |
| lmaldonado, jsepulveda | Chofer12345! | Chofer |

Para la vista de la familia prueba el código `CR-0001` con los dígitos `0142`.
**Cambia estas claves o crea tus usuarios antes de usar el sistema de verdad.**

Con PostgreSQL: copia `.env.example` a `.env`, ajusta los valores y no definas `USE_SQLITE`.

## Pruebas

```
USE_SQLITE=True python manage.py test
```
129 pruebas: modelos, solapamientos, etapas, stock, pedidos, traslados, permisos por rol (pantallas y API),
vista de la familia, avisos, buscador, Excel, calendario, precios, ventas (IVA, RUT, PDF) y datos de demostración.

## API

Bajo `/api/` (sesión o `SessionAuthentication`): `solicitudes`, `documentos`, `salas`, `vehiculos`, `traslados`,
`articulos`, `movimientos`, `pedidos`, `actividad`, `tarifas` y `ventas` (estas dos de solo lectura). Acciones: `POST /api/solicitudes/<id>/avanzar/`,
`POST /api/traslados/<id>/estado/` (`{"accion": "iniciar|retraso|completar"}`), `POST /api/pedidos/<id>/entregar/`.
Las reglas de negocio responden **400** con el motivo; sin permiso, **403**.

## Despliegue en AWS EC2 (una sola instancia, AWS Academy)

1. Lanza una instancia Ubuntu y abre en el Security Group los puertos **22** y **80**.
2. Conéctate por SSH, clona el repositorio como `~/cristorey` y ejecuta:
   ```bash
   cd ~/cristorey
   bash deploy/setup_ec2.sh
   venv/bin/python manage.py createsuperuser     # o: seed_demo para datos de ejemplo
   ```
3. Abre `http://IP-PUBLICA/`. El script instala PostgreSQL, Nginx y gunicorn (servicio `cristorey`),
   crea el `.env` con claves aleatorias y es idempotente (puedes repetirlo tras `git pull`).
4. Avisos diarios: `crontab -e` y pega el contenido de `deploy/cron_avisos.txt`.
5. Si la IP pública cambia (AWS Academy al reiniciar), actualiza `DJANGO_ALLOWED_HOSTS` y
   `DJANGO_CSRF_TRUSTED_ORIGINS` en `.env` y ejecuta `sudo systemctl restart cristorey`.
6. Para enviar correos reales completa `EMAIL_HOST`, `EMAIL_USER` y `EMAIL_PASSWORD` en `.env`; sin ellos se imprimen en consola.

## Estructura

```
config/                 ajustes, urls, wsgi
servicios/
  models.py             Sala, Vehiculo, Solicitud, Documento, Traslado, Articulo, Pedido, Movimiento, Actividad
  services.py           reglas de negocio (solapamientos, etapas, stock, avisos)
  permissions.py        roles y permisos (pantallas y API)
  views.py / forms.py   pantallas
  api.py / serializers.py   API REST
  admin.py              Django Admin personalizado
  estados.py            tabla única de estados (color + ícono + texto)
  management/commands/  seed_demo, avisar_documentos
  tests.py
templates/  static/     interfaz (guía de diseño Cristo Rey: verde menta claro, 15 px, foco visible)
deploy/                 setup_ec2.sh, servicio systemd, Nginx, cron
```
