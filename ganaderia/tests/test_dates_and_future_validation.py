from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.models import User
from ganaderia.models import Explotacion, Finca, Animal, Parto, Ubicacion
from ganaderia.forms import AnimalForm, PartoForm
from ganaderia.services.animal_services import registrar_parto


class DateValidationAndFormattingTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Fechas Test",
            codigo_rega="ES111222333444"
        )
        self.finca = Finca.objects.create(explotacion=self.explotacion, nombre="Finca Fechas")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion='PASTO')

        self.today = timezone.now().date()
        self.past_date = self.today - timedelta(days=500)
        self.future_date = self.today + timedelta(days=1)

        self.madre = Animal.objects.create(
            crotal="0100",
            sexo="H",
            fecha_nacimiento=self.past_date,
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

    def test_fecha_nacimiento_futura_fails_model_and_form(self):
        """1. Test de fecha de nacimiento futura: lanza ValidationError en modelo y formulario."""
        # Validación a nivel de modelo
        animal_futuro = Animal(
            crotal="9999",
            sexo="M",
            fecha_nacimiento=self.future_date,
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        with self.assertRaises(ValidationError):
            animal_futuro.full_clean()

        # Validación a nivel de formulario
        form_data = {
            'crotal': '9999',
            'sexo': 'M',
            'raza': 'RETINTA',
            'fecha_nacimiento': self.future_date.strftime('%Y-%m-%d'),
            'finca': self.finca.pk,
            'sub_ubicacion': 'PASTO',
        }
        form = AnimalForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('fecha_nacimiento', form.errors)

    def test_fecha_nacimiento_valida_success(self):
        """2. Test de fecha de nacimiento válida: hoy y fechas pasadas guardan con éxito."""
        animal_hoy = Animal.objects.create(
            crotal="0101",
            sexo="M",
            fecha_nacimiento=self.today,
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        self.assertEqual(animal_hoy.fecha_nacimiento, self.today)

        animal_pasado = Animal.objects.create(
            crotal="0102",
            sexo="H",
            fecha_nacimiento=self.today - timedelta(days=365),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        self.assertEqual(animal_pasado.fecha_nacimiento, self.today - timedelta(days=365))

    def test_parto_futuro_fails(self):
        """3. Test de parto futuro: intentar registrar un parto con fecha de mañana debe ser rechazado."""
        crias_data = [{'crotal': '0200', 'sexo': 'M', 'raza': 'RETINTA'}]

        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=self.madre,
                fecha_parto=self.future_date,
                crias=crias_data
            )

        # Formulario de parto
        form_data = {
            'madre': self.madre.pk,
            'fecha_parto': self.future_date.strftime('%Y-%m-%d'),
            'crotal_cria_1': '0200',
            'sexo_cria_1': 'M',
            'raza_cria_1': 'RETINTA',
        }
        form = PartoForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('fecha_parto', form.errors)

    def test_formato_visual_fechas_dd_mm_yyyy(self):
        """4. Test de formato visual: verificar que la respuesta renderizada en Ficha y Home formatea como DD/MM/AAAA."""
        fecha_especifica = date(2023, 5, 15)
        animal = Animal.objects.create(
            crotal="0300",
            sexo="H",
            fecha_nacimiento=fecha_especifica,
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        registrar_parto(
            madre=animal,
            fecha_parto=date(2025, 1, 20),
            crias=[{'crotal': '0301', 'sexo': 'M'}]
        )

        # Ficha del animal
        url_ficha = reverse('animal_detail', kwargs={'crotal': '0300'})
        res_ficha = self.client.get(url_ficha)
        self.assertEqual(res_ficha.status_code, 200)
        content_ficha = res_ficha.content.decode('utf-8')
        
        # Debe contener 15/05/2023 (nacimiento) y 20/01/2025 (parto)
        self.assertIn('15/05/2023', content_ficha)
        self.assertIn('20/01/2025', content_ficha)

        # Home / Censo
        url_home = reverse('home')
        res_home = self.client.get(url_home)
        self.assertEqual(res_home.status_code, 200)
        content_home = res_home.content.decode('utf-8')
        self.assertIn('20/01/2025', content_home)
