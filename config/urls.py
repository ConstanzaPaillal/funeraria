"""Rutas principales: admin, autenticación, API y la app de servicios funerarios."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),  # solo POST (protección CSRF)
    path("api-auth/", include("rest_framework.urls")),
    path("", include("servicios.urls")),
]

# Archivos subidos (si en el futuro se adjuntan documentos): en desarrollo se sirven así;
# en producción los sirve Nginx (ver deploy/).
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = "servicios.views.error_404"
handler403 = "servicios.views.error_403"
handler500 = "servicios.views.error_500"
