"""
Tests para autenticación obligatoria y endpoint público de healthcheck (UptimeRobot).
"""
from datetime import date
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from ganaderia.models import Explotacion, Finca, Animal, Ubicacion


class HealthCheckTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def test_healthcheck_public_success(self):
        """El endpoint /healthcheck/ debe ser accesible sin autenticación y responder 200 OK."""
        response = self.client.get(reverse('healthcheck'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8'), "OK")
        self.assertEqual(response['Content-Type'], "text/plain")

    def test_healthcheck_database_error_returns_503(self):
        """Si la conexión a la base de datos falla, /healthcheck/ responde 503 UNHEALTHY."""
        with patch('django.db.connection.cursor', side_effect=Exception("Database connection timeout")):
            response = self.client.get(reverse('healthcheck'))
            self.assertEqual(response.status_code, 503)
            self.assertIn("UNHEALTHY: Database connection timeout", response.content.decode('utf-8'))
            self.assertEqual(response['Content-Type'], "text/plain")


class AuthenticationAndViewsProtectionTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = "familia_test"
        self.password = "Secr3tP@ssw0rd!"
        self.user = User.objects.create_user(
            username=self.username,
            password=self.password
        )
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Auth Test",
            codigo_rega="ES999000111222"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Familiar"
        )
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="PASTO")
        self.animal = Animal.objects.create(
            crotal="9901",
            sexo="H",
            fecha_nacimiento=date(2021, 1, 1),
            finca=self.finca,
            sub_ubicacion="PASTO",
            estado_vital="VIVO"
        )

    def test_unauthenticated_access_redirects_to_login(self):
        """Las vistas operativas deben redirigir a login si el usuario es anónimo."""
        protected_urls = [
            reverse('home'),
            reverse('animal_list'),
            reverse('animal_detail', kwargs={'crotal': self.animal.crotal}),
            reverse('animal_create'),
            reverse('animal_baja', kwargs={'crotal': self.animal.crotal}),
            reverse('animal_traslado', kwargs={'crotal': self.animal.crotal}),
            reverse('parto_create'),
            reverse('finca_list'),
            reverse('finca_create'),
            reverse('incidencias'),
            reverse('explotacion_edit'),
            reverse('exportar_csv'),
            reverse('exportar_excel'),
        ]

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response.url)

    def test_login_page_renders_custom_template(self):
        """La página de login muestra el formulario estilizado con el título requerido."""
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/login.html')
        self.assertContains(response, "Gestión Ganadera - Acceso Exclusivo")
        self.assertContains(response, "Iniciar Sesión")
        self.assertContains(response, "name=\"username\"")
        self.assertContains(response, "name=\"password\"")

    def test_login_invalid_credentials_shows_error(self):
        """Credenciales incorrectas muestran un mensaje de error claro en el formulario."""
        response = self.client.post(reverse('login'), {
            'username': self.username,
            'password': 'wrong_password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Credenciales incorrectas")

    def test_login_success_and_redirect(self):
        """Inicio de sesión exitoso redirige a 'home' y permite el acceso a vistas protegidas."""
        response = self.client.post(reverse('login'), {
            'username': self.username,
            'password': self.password,
        })
        self.assertRedirects(response, reverse('home'))

        # Ahora el cliente está autenticado y puede acceder a home
        home_res = self.client.get(reverse('home'))
        self.assertEqual(home_res.status_code, 200)
        self.assertContains(home_res, self.username)
        self.assertContains(home_res, "Cerrar sesión")

    def test_logout_post_redirects_to_login(self):
        """Hacer POST a logout cierra la sesión y redirige a login."""
        self.client.force_login(self.user)
        home_res = self.client.get(reverse('home'))
        self.assertEqual(home_res.status_code, 200)

        logout_res = self.client.post(reverse('logout'))
        self.assertRedirects(logout_res, reverse('login'))

        # Tras el logout, el acceso a home vuelve a exigir login
        home_res_after = self.client.get(reverse('home'))
        self.assertEqual(home_res_after.status_code, 302)
        self.assertIn('/accounts/login/', home_res_after.url)
