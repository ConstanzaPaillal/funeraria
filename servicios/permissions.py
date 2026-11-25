"""
Roles y permisos de Cristo Rey.

Roles (se asignan mediante Grupos de Django):
  - admin     -> superusuario: todo, incluida la gestión de usuarios
  - recepcion -> grupo "Recepcionista": solicitudes, documentos, salas, asignación de choferes
  - bodega    -> grupo "Bodega": inventario y pedidos de materiales
  - chofer    -> grupo "Chofer": ve y actualiza solo sus traslados

La misma regla se usa en las vistas con templates (RolesMixin) y en la API (PermisoPorRol).
"""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from rest_framework.permissions import SAFE_METHODS, BasePermission

ADMIN, RECEPCION, BODEGA, CHOFER = "admin", "recepcion", "bodega", "chofer"
GRUPOS = {"Recepcionista": RECEPCION, "Bodega": BODEGA, "Chofer": CHOFER}
GRUPO_DE_ROL = {v: k for k, v in GRUPOS.items()}
ETIQUETAS = {ADMIN: "Administrador", RECEPCION: "Recepción", BODEGA: "Bodega", CHOFER: "Chofer"}
TODOS = {ADMIN, RECEPCION, BODEGA, CHOFER}


def roles_de(user):
    """Conjunto de roles del usuario (cacheado en el objeto user durante la petición)."""
    if not user.is_authenticated or not user.is_active:
        return set()
    if not hasattr(user, "_roles_cache"):
        roles = {ADMIN} if user.is_superuser else set()
        roles |= {GRUPOS[g] for g in user.groups.values_list("name", flat=True) if g in GRUPOS}
        user._roles_cache = roles
    return user._roles_cache


def rol_principal(user):
    r = roles_de(user)
    for rol in (ADMIN, RECEPCION, BODEGA, CHOFER):
        if rol in r:
            return rol
    return None


def tiene_rol(user, permitidos):
    roles = roles_de(user)
    return ADMIN in roles or bool(roles & set(permitidos))


class PermisoPorRol(BasePermission):
    """
    Cada ViewSet declara `roles_lectura` y `roles_escritura`.
    Métodos seguros (GET...) exigen lectura o escritura; el resto exige escritura.
    """
    message = "Tu rol no tiene permiso para realizar esta acción."

    def has_permission(self, request, view):
        lectura = set(getattr(view, "roles_lectura", TODOS))
        escritura = set(getattr(view, "roles_escritura", set()))
        necesarios = lectura | escritura if request.method in SAFE_METHODS else escritura
        return tiene_rol(request.user, necesarios)


class RolesMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Para vistas con templates: `roles_permitidos` = roles que pueden entrar. Sin permiso -> 403."""
    roles_permitidos = TODOS

    def test_func(self):
        return tiene_rol(self.request.user, self.roles_permitidos)
