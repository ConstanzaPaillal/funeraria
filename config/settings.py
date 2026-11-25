"""
Configuración de Django para Cristo Rey.

Toda la configuración sensible (clave secreta, base de datos, hosts) se lee
desde variables de entorno / archivo .env, para no dejar secretos en el repo.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(nombre, defecto=False):
    return os.environ.get(nombre, str(defecto)).strip().lower() in ("1", "true", "yes", "si")


def env_list(nombre, defecto=""):
    return [x.strip() for x in os.environ.get(nombre, defecto).split(",") if x.strip()]


# --------------------------------------------------------------------------
# Seguridad
# --------------------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "inseguro-solo-para-desarrollo-local")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if not DEBUG and SECRET_KEY.startswith("inseguro"):
    raise RuntimeError("Define DJANGO_SECRET_KEY en el .env antes de desplegar con DEBUG=False.")

# Cookies/redirección HTTPS solo cuando realmente se sirve por HTTPS.
HTTPS = env_bool("DJANGO_HTTPS", False)
if not DEBUG:
    SESSION_COOKIE_SECURE = HTTPS
    CSRF_COOKIE_SECURE = HTTPS
    SECURE_SSL_REDIRECT = HTTPS
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if HTTPS else None
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8  # la sesión expira a las 8 horas
SESSION_SAVE_EVERY_REQUEST = False

# --------------------------------------------------------------------------
# Aplicaciones y middleware
# --------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "servicios",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    # Sirve /static/ directamente desde gunicorn/Nginx-less (tras collectstatic)
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "servicios.context_processors.roles",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Base de datos: PostgreSQL (criterio 1)
# --------------------------------------------------------------------------
if env_bool("USE_SQLITE", False):
    # Solo para pruebas rápidas locales; la entrega usa PostgreSQL.
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "cristorey"),
            "USER": os.environ.get("DB_USER", "cristorey"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Autenticación
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "servicios.permissions.PermisoPorRol",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "1000/hour"},
    "EXCEPTION_HANDLER": "servicios.api.manejador",
}
if not DEBUG:
    # En producción solo JSON (sin la interfaz navegable de DRF).
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = ["rest_framework.renderers.JSONRenderer"]

# --------------------------------------------------------------------------
# Internacionalización
# --------------------------------------------------------------------------
LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Archivos estáticos y media (resultados de exámenes)
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if DEBUG:
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --------------------------------------------------------------------------
# Correo para avisar a las familias
# Sin EMAIL_HOST: los correos se imprimen en la consola (ideal para demo).
# --------------------------------------------------------------------------
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Cristo Rey <no-reply@cristorey.local>")
if os.environ.get("EMAIL_HOST"):
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": os.environ["EMAIL_HOST"],
                "port": int(os.environ.get("EMAIL_PORT", "587")),
                "username": os.environ.get("EMAIL_USER", ""),
                "password": os.environ.get("EMAIL_PASSWORD", ""),
                "use_tls": True,
            },
        },
    }
else:
    MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

# --------------------------------------------------------------------------
# Reglas del negocio
# --------------------------------------------------------------------------
DURACION_TRASLADO_MIN = 90        # duración por defecto de un traslado
DIAS_AVISO_DOCUMENTOS = 3         # se avisa a la familia si la ceremonia es dentro de N días
HORAS_URGENTE = 24                # sin documentos y ceremonia en menos de N horas => "Urgente"
FAMILIA_MAX_INTENTOS = 8          # intentos de consulta pública por IP en la ventana
FAMILIA_VENTANA_SEG = 15 * 60     # ventana de intentos (15 minutos)

# Ventas: los precios del catálogo incluyen IVA (Chile: 19 %).
IVA = 0.19
# Datos del emisor que aparecen en boletas y facturas (reemplázalos por los reales de la funeraria).
EMPRESA = {
    "razon_social": os.environ.get("EMPRESA_RAZON_SOCIAL", "Funeraria Cristo Rey SpA"),
    "rut": os.environ.get("EMPRESA_RUT", "76.000.000-0"),
    "giro": "Servicios funerarios",
    "direccion": os.environ.get("EMPRESA_DIRECCION", "Temuco, Región de La Araucanía"),
    "telefono": "+56 45 255 0100",
}

# Transbank Webpay Plus. Por defecto usa el ambiente de INTEGRACIÓN (credenciales públicas de pruebas de Transbank,
# no se cobra dinero real). Para producción define TRANSBANK_ENV=production y las claves entregadas por Transbank.
TRANSBANK_ENV = os.environ.get("TRANSBANK_ENV", "integration")
TRANSBANK_COMMERCE_CODE = os.environ.get("TRANSBANK_COMMERCE_CODE", "597055555532")
TRANSBANK_API_KEY = os.environ.get("TRANSBANK_API_KEY", "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C")
