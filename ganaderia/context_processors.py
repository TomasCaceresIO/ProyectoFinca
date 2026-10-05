from django.utils import timezone
from ganaderia.models import Parto


def global_context(request):
    """Procesador de contexto global para proveer la fecha actual y contador de incidencias activas."""
    try:
        incidencias_count = Parto.objects.filter(
            alerta_intervalo=True,
            madre__estado_vital='VIVO'
        ).count()
    except Exception:
        incidencias_count = 0

    return {
        'hoy': timezone.now().date(),
        'incidencias_count': incidencias_count,
    }
