"""
Middleware de primer inicio.
Si no existe ninguna Explotación en la base de datos,
redirige todas las peticiones a /setup/ (Onboarding Wizard).
"""
from django.shortcuts import redirect


class OnboardingMiddleware:
    """
    Middleware que intercepta peticiones cuando no hay explotación configurada.
    Redirige obligatoriamente al wizard de configuración inicial.
    """
    ALLOWED_PATHS = [
        '/setup/',
        '/admin/',
        '/static/',
        '/media/',
        '/healthcheck/',
        '/accounts/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_setup(request):
            return redirect('/setup/')
        return self.get_response(request)

    def _needs_setup(self, request) -> bool:
        """Determina si se necesita redirigir al wizard."""
        path = request.path_info
        for allowed in self.ALLOWED_PATHS:
            if path.startswith(allowed):
                return False
        
        try:
            from .models import Explotacion
            return not Explotacion.objects.exists()
        except Exception:
            return False
