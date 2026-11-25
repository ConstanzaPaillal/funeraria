"""
Avisa por correo a las familias que aún no entregan documentos y cuya ceremonia es pronto.

Uso:
    python manage.py avisar_documentos              # usa DIAS_AVISO_DOCUMENTOS (3)
    python manage.py avisar_documentos --dias 5
    python manage.py avisar_documentos --simular    # solo muestra cuántos enviaría

Pensado para ejecutarse a diario con cron (ver deploy/cron_avisos.txt). Envía como máximo un aviso
por solicitud cada 24 horas.
"""
from django.core.management.base import BaseCommand

from servicios import services


class Command(BaseCommand):
    help = "Envía el aviso de documentos pendientes a las familias."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=None, help="Anticipación en días (por defecto, la de settings).")
        parser.add_argument("--simular", action="store_true", help="No envía ni modifica nada.")

    def handle(self, *args, **opts):
        enviados, fallidos = services.avisar_documentos(opts["dias"], simular=opts["simular"])
        etiqueta = "Se enviarían" if opts["simular"] else "Enviados"
        self.stdout.write(self.style.SUCCESS(f"{etiqueta}: {enviados}"))
        if fallidos:
            self.stdout.write(self.style.WARNING(f"Fallidos: {fallidos}"))
