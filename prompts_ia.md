# Apoyo de Inteligencia Artificial — Cristo Rey

Herramienta utilizada: **Claude** (Anthropic), mediante Claude Code.
Esta bitácora resume las consultas realizadas y el apoyo recibido en cada etapa.
El código fue revisado, probado y comprendido antes de incluirlo en el proyecto.

## 1. Planteamiento y diseño
**Consulta:** Pedí adaptar el sistema PetCare (clínica veterinaria) a una funeraria llamada «Cristo Rey»,
con 1 administrador, 2 recepcionistas, 1 de bodega y 2 choferes, siguiendo las pautas de diseño de mi informe (sección 6.2).
**Apoyo:** Prototipo HTML de la interfaz y luego el sistema completo: una app `servicios`, modelo de datos
(Sala, Vehículo, Solicitud, Documento, Traslado, Artículo, Pedido, Movimiento, Actividad) y definición de roles.

## 2. Modelos y base de datos
**Consulta:** Conexión a PostgreSQL con variables de entorno y migraciones.
**Apoyo:** `config/settings.py` (python-dotenv), `migrations/0001_initial.py`, validaciones en los modelos
(teléfono, código de solicitud automático, solapamiento de salas, traslados y estados).

## 3. Reglas de negocio
**Consulta:** Evitar choques de horario de salas, vehículos y choferes; no avanzar sin documentos; stock sin negativos.
**Apoyo:** `servicios/services.py` con transacciones y bloqueos de fila (`select_for_update`), estado calculado de
la solicitud, entrega de pedidos «todo o nada» y registro de actividad.

## 4. Django Admin
**Consulta:** Administrador personalizado para todos los modelos.
**Apoyo:** `servicios/admin.py` con filtros, búsqueda, inlines, movimientos y actividad de solo lectura, y acciones masivas.

## 5. CRUD, vistas y API
**Consulta:** CRUD con templates y API REST por rol.
**Apoyo:** vistas basadas en clases, formularios, serializers y ViewSets de DRF bajo `/api/`; errores de negocio como HTTP 400.

## 6. Seguridad
**Consulta:** Autenticación y permisos por rol, y una vista pública para la familia.
**Apoyo:** `permissions.py` (misma regla en pantallas y API), CSRF, páginas 403/404/500 propias, comparación en tiempo
constante y límite de intentos en la consulta de la familia, secretos fuera del repositorio.

## 7. Interfaz
**Consulta:** Aplicar las pautas del informe: color, tipografía, accesibilidad y estados.
**Apoyo:** `static/css/cristorey.css`, templates con una tabla única de estados (color + ícono + texto), foco visible,
modo claro/oscuro y diseño adaptable a móvil; verificado con capturas en un navegador real.

## 8. Avisos automáticos
**Consulta:** Aviso por correo a las familias con documentos pendientes.
**Apoyo:** comando `manage.py avisar_documentos` (con `--simular`), pensado para cron.

## 9. Pruebas
**Consulta:** Pruebas automáticas.
**Apoyo:** `servicios/tests.py` (129 pruebas, todas pasan con `python manage.py test`).

## 10. Despliegue en AWS EC2
**Consulta:** Despliegue en una instancia única.
**Apoyo:** `deploy/setup_ec2.sh`, servicio systemd de gunicorn, Nginx y cron.

## 11. Calendario, buscador, Excel, precios y ventas
**Consulta:** Pedí un calendario, un buscador de personas con filtros y filas desplegables, descarga en Excel con los datos de
los fallecidos y la persona a cargo, precios de féretros (básico, clásico, premium) y una opción de venta para recepción y
administrador que genere boleta o factura.
**Apoyo:** vista de calendario mensual por rol, `services.buscar_solicitudes` (misma búsqueda para pantalla y Excel),
`exportacion.py` (openpyxl), modelos `Tarifa`, `Venta` y `VentaItem`, cálculo de IVA, validación de RUT, PDF con reportlab,
anulación solo para administrador y 25 pruebas nuevas.

## 12. AJAX, tema verde, imágenes y gestor de ausencias
**Consulta:** Pedí que la página recargue menos (AJAX), cambiar los colores a verde, poder colocar imágenes de los productos
y un gestor de ausencias para el administrador (quiénes faltarán o pidieron permiso).
**Apoyo:** `static/js/cristorey.js` (mejora progresiva con fetch, búsqueda en vivo, historial y accesibilidad), paleta verde
con contrastes calculados, `Articulo.imagen` con validación (tipo, peso y nombre aleatorio), modelo `Ausencia` con solicitud,
aprobación, reglas de negocio (solapamientos, traslados de choferes), API, panel y calendario, y 27 pruebas nuevas
verificadas también en un navegador real.

## 13. Lista de pendientes: urnas, servicios, perfiles, etiquetas, abonos y Webpay
**Consulta:** Desde mi bloc de notas pedí administrar urnas (medidas y fotos), servicios y tiempos, perfiles, abono de reserva,
permisos y ausencias, cambiar el color a uno claro, registrar etiquetas e integrar Transbank.
**Apoyo:** campos de medidas y galería de fotos, tarifas con duración, perfiles con RUT validado, etiquetas de color, abonos con saldo,
integración con Webpay Plus (ambiente de pruebas, confirmación idempotente y verificación de monto, probada con simulaciones),
paleta verde claro con contraste calculado y 20 pruebas nuevas.

---
> Nota: esta lista resume el apoyo recibido; si usaste consultas adicionales, agrégalas aquí
> con su fecha antes de entregar.
