from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.models import User
from ganaderia.models import Explotacion, Finca, Animal, Parto, Ubicacion
from ganaderia.forms import AnimalForm
from ganaderia.services.animal_services import registrar_parto


class AnimalEditRestrictionsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Edit Tests",
            codigo_rega="ES888777666555"
        )
        self.finca = Finca.objects.create(explotacion=self.explotacion, nombre="Finca Edit")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion='PASTO')
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion='CEBADERO')

        # Madre reproductora (nacida en 2020)
        self.hembra_con_partos = Animal.objects.create(
            crotal="1111",
            sexo="H",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        # Parto en 01/01/2025
        registrar_parto(
            madre=self.hembra_con_partos,
            fecha_parto=date(2025, 1, 1),
            crias=[{'crotal': '1112', 'sexo': 'M'}]
        )
        self.cria_hijo = Animal.objects.get(crotal="1112")

        # Hembra joven sin partos
        self.hembra_sin_partos = Animal.objects.create(
            crotal="2222",
            sexo="H",
            fecha_nacimiento=date(2024, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

    def test_bloqueo_cambio_sexo_con_partos(self):
        """1. test_bloqueo_cambio_sexo_con_partos: Hembra con partos no puede cambiar sexo a 'M'."""
        form_data = {
            'crotal': '1111',
            'sexo': 'M',  # Intento ilegal de cambiar a Macho
            'raza': 'RETINTA',
            'fecha_nacimiento': '2020-01-01',
            'finca': self.finca.pk,
            'sub_ubicacion': 'PASTO',
        }
        form = AnimalForm(data=form_data, instance=self.hembra_con_partos)
        self.assertFalse(form.is_valid())
        self.assertIn('sexo', form.errors)
        self.assertIn('descendencia', form.errors['sexo'][0])

    def test_permite_cambio_sexo_sin_partos(self):
        """2. test_permite_cambio_sexo_sin_partos: Hembra sin partos puede cambiar sexo a 'M'."""
        form_data = {
            'crotal': '2222',
            'sexo': 'M',
            'raza': 'RETINTA',
            'fecha_nacimiento': '2024-01-01',
            'finca': self.finca.pk,
            'sub_ubicacion': 'PASTO',
        }
        form = AnimalForm(data=form_data, instance=self.hembra_sin_partos)
        self.assertTrue(form.is_valid(), form.errors)
        animal_actualizado = form.save()
        self.assertEqual(animal_actualizado.sexo, 'M')

    def test_fecha_nacimiento_incompatible_con_partos(self):
        """3. test_fecha_nacimiento_incompatible_con_partos: Nueva fecha dejaría < 18 meses al parto -> Debe fallar."""
        # Hembra tuvo parto el 01/01/2025. Intentar cambiar nacimiento a 01/06/2024 (7 meses al parir).
        form_data = {
            'crotal': '1111',
            'sexo': 'H',
            'raza': 'RETINTA',
            'fecha_nacimiento': '2024-06-01',
            'finca': self.finca.pk,
            'sub_ubicacion': 'PASTO',
        }
        form = AnimalForm(data=form_data, instance=self.hembra_con_partos)
        self.assertFalse(form.is_valid())
        self.assertIn('fecha_nacimiento', form.errors)

    def test_bloqueo_autopaternidad_y_circulares(self):
        """4. test_bloqueo_autopaternidad_y_circulares: Asignarse a sí misma o a una hija como madre debe fallar."""
        # Intento 1: Auto-maternidad
        form_self = AnimalForm(data={
            'crotal': '1111', 'sexo': 'H', 'raza': 'RETINTA',
            'fecha_nacimiento': '2020-01-01', 'finca': self.finca.pk,
            'sub_ubicacion': 'PASTO', 'madre': self.hembra_con_partos.pk
        }, instance=self.hembra_con_partos)
        self.assertFalse(form_self.is_valid())
        self.assertIn('madre', form_self.errors)

        # Intento 2: Asignar a su propia hija (la cría hembra nacida en el parto) como madre
        cria_hembra = Animal.objects.create(
            crotal="3333", sexo="H", fecha_nacimiento=date(2025, 1, 1),
            estado_vital="VIVO", finca=self.finca, sub_ubicacion="PASTO", madre=self.hembra_con_partos
        )
        form_circular = AnimalForm(data={
            'crotal': '1111', 'sexo': 'H', 'raza': 'RETINTA',
            'fecha_nacimiento': '2020-01-01', 'finca': self.finca.pk,
            'sub_ubicacion': 'PASTO', 'madre': cria_hembra.pk
        }, instance=self.hembra_con_partos)
        self.assertFalse(form_circular.is_valid())
        self.assertIn('madre', form_circular.errors)

    def test_sellado_cebadero_en_edicion(self):
        """5. test_sellado_cebadero_en_edicion: Cambiar recinto a CEBADERO sella fecha_entrada_cebadero."""
        self.assertIsNone(self.hembra_sin_partos.fecha_entrada_cebadero)

        form_data = {
            'crotal': '2222',
            'sexo': 'H',
            'raza': 'RETINTA',
            'fecha_nacimiento': '2024-01-01',
            'finca': self.finca.pk,
            'sub_ubicacion': 'CEBADERO',
        }
        form = AnimalForm(data=form_data, instance=self.hembra_sin_partos)
        self.assertTrue(form.is_valid(), form.errors)
        animal_editado = form.save()

        self.assertEqual(animal_editado.sub_ubicacion, 'CEBADERO')
        self.assertEqual(animal_editado.fecha_entrada_cebadero, timezone.now().date())

    def test_no_partial_mutation_on_invalid_form_submission(self):
        """6. test_no_partial_mutation_on_invalid_form_submission: Envío fallido de formulario no muta BD ni context."""
        url = reverse('animal_edit', kwargs={'crotal': self.hembra_sin_partos.crotal})
        response = self.client.post(url, {
            'crotal': '2222',
            'sexo': 'H',
            'raza': 'RETINTA',
            'fecha_nacimiento': '2099-01-01',  # Fecha invalida (futura)
            'finca': self.finca.pk,
            'sub_ubicacion': 'CEBADERO',
        })
        self.assertEqual(response.status_code, 200)
        self.hembra_sin_partos.refresh_from_db()
        self.assertEqual(self.hembra_sin_partos.sub_ubicacion, 'PASTO')
        self.assertIsNone(self.hembra_sin_partos.fecha_entrada_cebadero)
        context_animal = response.context['animal']
        self.assertEqual(context_animal.sub_ubicacion, 'PASTO')

