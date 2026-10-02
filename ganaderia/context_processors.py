from django.utils import timezone

def global_context(request):
    """Procesador de contexto global para proveer la fecha actual."""
    return {
        'hoy': timezone.now().date()
    }
