"""
Middleware de primer inicio.
Si no existe ninguna Explotación en la base de datos,
redirige todas las peticiones a /setup/ (Onboarding Wizard).
"""
from django.shortcuts import redirect
from django.urls import reverse


class OnboardingMiddleware:
    """
    Middleware que intercepta peticiones cuando no hay explotación configurada.
    Redirige obligatoriamente al wizard de configuración inicial.
    """
    # URLs que están permitidas sin configuración
    ALLOWED_PATHS = [
        '/setup/',
        '/admin/',
        '/static/',
        '/media/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Importación diferida para evitar problemas en migraciones
        if self._needs_setup(request):
            return redirect('/setup/')
        return self.get_response(request)

    def _needs_setup(self, request) -> bool:
        """Determina si se necesita redirigir al wizard."""
        # Permitir siempre las rutas del sistema
        for path in self.ALLOWED_PATHS:
            if request.path.startswith(path):
                return False
        
        try:
            from .models import Explotacion
            return not Explotacion.objects.exists()
        except Exception:
            # Si la tabla no existe aún (pre-migrate), no bloquear
            return False
