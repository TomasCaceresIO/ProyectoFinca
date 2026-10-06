from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from datetime import date
from ganaderia.models import Explotacion, Finca, Animal, Parto


class MiddlewareAndPerformanceTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)

    def test_onboarding_middleware_redirection_and_no_loop(self):
        """Sin explotación en BD: Petición a / devuelve 302 a /setup/. Petición a /setup/ devuelve 200 (sin bucle)."""
        Explotacion.objects.all().delete()

        res_home = self.client.get(reverse('home'))
        self.assertEqual(res_home.status_code, 302)
        self.assertIn('/setup/', res_home.url)

        res_setup = self.client.get(reverse('setup_wizard'))
        self.assertEqual(res_setup.status_code, 200)

    def test_home_page_queries_performance(self):
        """Con explotación creada: Home responde 200 y la consulta no sufre problema de N+1 queries."""
        exp = Explotacion.objects.create(nombre="Explotación Performance", codigo_rega="ES777777777777")
        finca = Finca.objects.create(explotacion=exp, nombre="Finca Perf")

        # Crear 10 animales
        for i in range(1, 11):
            crotal = f"{i:04d}"
            Animal.objects.create(
                crotal=crotal,
                sexo="H" if i % 2 == 0 else "M",
                fecha_nacimiento=date(2021, 1, 1),
                finca=finca,
                sub_ubicacion="PASTO"
            )

        # La consulta debe ejecutar un número constante de queries (12 con sesión autenticada y context processor) sin sufrir N+1
        with self.assertNumQueries(12):
            res = self.client.get(reverse('home'))
            self.assertEqual(res.status_code, 200)
