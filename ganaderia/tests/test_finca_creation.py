from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from ganaderia.models import Explotacion, Finca, Ubicacion


class FincaCreationTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Fincas",
            codigo_rega="ES111122223333"
        )

    def test_create_finca_with_enclosures(self):
        """Verificar la creación de una nueva finca con sus recintos."""
        url = reverse('finca_create')
        data = {
            'nombre': 'Finca Los Olivos',
            'cebadero': 'on',
            'apartado': 'on',
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('finca_list'))

        finca = Finca.objects.get(nombre='Finca Los Olivos')
        self.assertEqual(finca.explotacion, self.explotacion)

        # Verificar recintos creados (Pasto obligatorio, Cebadero y Apartado por checkbox)
        recintos = list(Ubicacion.objects.filter(finca=finca).values_list('tipo_ubicacion', flat=True))
        self.assertIn('PASTO', recintos)
        self.assertIn('CEBADERO', recintos)
        self.assertIn('APARTADO', recintos)
        self.assertEqual(len(recintos), 3)

    def test_create_finca_only_pasto_enclosure(self):
        """Verificar creación de finca con solo recinto Pasto si cebadero y apartado no están marcados."""
        url = reverse('finca_create')
        data = {
            'nombre': 'Finca Solo Pasto',
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        finca = Finca.objects.get(nombre='Finca Solo Pasto')
        recintos = list(Ubicacion.objects.filter(finca=finca).values_list('tipo_ubicacion', flat=True))
        self.assertEqual(recintos, ['PASTO'])
