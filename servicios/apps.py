from django.apps import AppConfig
from django.db.models.signals import post_migrate


def crear_grupos(sender, **kwargs):
    """
    Tras migrar crea los grupos de roles con sus permisos de modelo, para que
    también funcionen en Django Admin (usuarios con is_staff).
    """
    from django.contrib.auth.management import create_permissions
    from django.contrib.auth.models import Group, Permission

    create_permissions(sender, verbosity=0)  # asegura que los permisos ya existan

    todos = ["add", "change", "delete", "view"]
    definicion = {
        "Recepcionista": {
            "solicitud": todos, "documento": todos, "traslado": todos, "sala": ["view"],
            "vehiculo": ["view"], "pedido": ["view"],
        },
        "Bodega": {
            "articulo": todos, "movimiento": ["view", "add"], "pedido": todos, "pedidoitem": todos,
            "solicitud": ["view"],
        },
        "Chofer": {"traslado": ["view", "change"], "solicitud": ["view"]},
    }
    for nombre, modelos in definicion.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        codenames = [f"{accion}_{modelo}" for modelo, acciones in modelos.items() for accion in acciones]
        permisos = Permission.objects.filter(content_type__app_label="servicios", codename__in=codenames)
        grupo.permissions.set(permisos)


class ServiciosConfig(AppConfig):
    name = "servicios"
    verbose_name = "Funeraria Cristo Rey"

    def ready(self):
        post_migrate.connect(crear_grupos, sender=self)
