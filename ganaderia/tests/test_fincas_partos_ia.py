"""
Tests para asignación y selección explícita de Finca en partos (IA y formularios tradicionales).
"""
from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

from ganaderia.models import Explotacion, Finca, Animal, Parto, Ubicacion
from ganaderia.services.ai_assistant import _extraer_comando_parto_regex
from ganaderia.services.animal_services import registrar_parto


class FincasPartosIATestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Fincas Partos",
            codigo_rega="ES123456789777"
        )
        self.finca_a = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Alfa"
        )
        self.finca_b = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Beta"
        )
        # Finca Alfa tiene Pasto y Cebadero
        Ubicacion.objects.create(finca=self.finca_a, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca_a, tipo_ubicacion="CEBADERO")

        # Finca Beta SOLO tiene Pasto (sin Cebadero)
        Ubicacion.objects.create(finca=self.finca_b, tipo_ubicacion="PASTO")

        # Madre adulta en Finca Alfa
        self.madre = Animal.objects.create(
            crotal="1001",
            sexo="H",
            raza="Retinta",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="VIVO",
            finca=self.finca_a,
            sub_ubicacion="PASTO"
        )

    def test_ai_parto_hereda_finca_de_madre_por_defecto(self):
        """1. Comando sin finca -> La cría se crea en la misma finca que la madre (Finca Alfa)."""
        url_preview = reverse('asistente_preview')
        res_preview = self.client.post(url_preview, {
            'texto': 'La 1001 parió hoy ternera 2001 limusina'
        })
        self.assertEqual(res_preview.status_code, 200)
        self.assertEqual(res_preview.context['finca_destino'], self.finca_a)

        url_ejecutar = reverse('asistente_ejecutar')
        res_ejecutar = self.client.post(url_ejecutar, {
            'crotal_madre': '1001',
            'fecha_parto': '2026-10-03',
            'cria_crotal': '2001',
            'cria_sexo': 'H',
            'cria_raza': 'Limusina',
            'cria_recinto': 'PASTO',
            'cria_finca_id': '',  # Vacío -> hereda de madre
        })
        self.assertEqual(res_ejecutar.status_code, 302)

        cria = Animal.objects.get(crotal="2001")
        self.assertEqual(cria.finca, self.finca_a)
        self.assertEqual(cria.sub_ubicacion, 'PASTO')

    def test_ai_parto_con_finca_explicita_diferente(self):
        """2. Madre en Finca Alfa, comando indica Finca Beta -> Cría se registra en Finca Beta y madre permanece en Finca Alfa."""
        url_preview = reverse('asistente_preview')
        res_preview = self.client.post(url_preview, {
            'texto': 'La 1001 parió hoy ternera 2002 retinta en Finca Beta'
        })
        self.assertEqual(res_preview.status_code, 200)
        self.assertEqual(res_preview.context['finca_destino'], self.finca_b)

        url_ejecutar = reverse('asistente_ejecutar')
        res_ejecutar = self.client.post(url_ejecutar, {
            'crotal_madre': '1001',
            'fecha_parto': '2026-10-03',
            'cria_crotal': '2002',
            'cria_sexo': 'H',
            'cria_raza': 'Retinta',
            'cria_recinto': 'PASTO',
            'cria_finca_id': str(self.finca_b.pk),
        })
        self.assertEqual(res_ejecutar.status_code, 302)

        cria = Animal.objects.get(crotal="2002")
        self.assertEqual(cria.finca, self.finca_b)
        self.madre.refresh_from_db()
        self.assertEqual(self.madre.finca, self.finca_a)

    def test_ai_preview_permite_modificar_finca_en_modal(self):
        """3. Enviar preview y alterar cria_finca_id antes de confirmar -> Cría se persiste en la finca seleccionada."""
        url_preview = reverse('asistente_preview')
        res_preview = self.client.post(url_preview, {
            'texto': 'La 1001 parió hoy ternero 2003 retinta'
        })
        self.assertEqual(res_preview.status_code, 200)
        # El modal ofrece cambiar la finca; simulamos que el usuario selecciona Finca Beta
        url_ejecutar = reverse('asistente_ejecutar')
        res_ejecutar = self.client.post(url_ejecutar, {
            'crotal_madre': '1001',
            'fecha_parto': '2026-10-03',
            'cria_crotal': '2003',
            'cria_sexo': 'M',
            'cria_raza': 'Retinta',
            'cria_recinto': 'PASTO',
            'cria_finca_id': str(self.finca_b.pk),
        })
        self.assertEqual(res_ejecutar.status_code, 302)

        cria = Animal.objects.get(crotal="2003")
        self.assertEqual(cria.finca, self.finca_b)

    def test_parto_tradicional_asigna_finca_seleccionada(self):
        """4. Registro vía formulario web con selección de finca -> Cría ubicada correctamente con fecha cebadero sellada si aplica."""
        url_parto = reverse('parto_create')
        data = {
            'madre': self.madre.pk,
            'fecha_parto': '2025-06-01',
            'crotal_cria_1': '2004',
            'sexo_cria_1': 'M',
            'raza_cria_1': 'Retinta',
            'finca_cria_1': self.finca_a.pk,
            'recinto_cria_1': 'CEBADERO',
        }
        res = self.client.post(url_parto, data)
        self.assertEqual(res.status_code, 302)

        cria = Animal.objects.get(crotal="2004")
        self.assertEqual(cria.finca, self.finca_a)
        self.assertEqual(cria.sub_ubicacion, 'CEBADERO')
        self.assertEqual(cria.fecha_entrada_cebadero, date(2025, 6, 1))

    def test_validacion_recinto_inactivo_en_finca(self):
        """5. Intentar ubicar cría en Cebadero de Finca Beta (sin cebadero) -> El sistema previene el estado inválido."""
        # Vía servicio directamente
        with self.assertRaises(ValidationError) as ctx:
            registrar_parto(
                madre=self.madre,
                fecha_parto=date(2025, 1, 1),
                crias=[{
                    'crotal': '2005',
                    'sexo': 'M',
                    'finca': self.finca_b,
                    'sub_ubicacion': 'CEBADERO',
                }]
            )
        self.assertIn("no está habilitado en la finca 'Finca Beta'", str(ctx.exception))

        # Vía formulario tradicional
        url_parto = reverse('parto_create')
        data = {
            'madre': self.madre.pk,
            'fecha_parto': '2025-01-01',
            'crotal_cria_1': '2006',
            'sexo_cria_1': 'M',
            'raza_cria_1': 'Retinta',
            'finca_cria_1': self.finca_b.pk,
            'recinto_cria_1': 'CEBADERO',
        }
        res_form = self.client.post(url_parto, data)
        self.assertEqual(res_form.status_code, 200)
        self.assertIn('recinto_cria_1', res_form.context['form'].errors)
        self.assertIn("no está habilitado en la finca 'Finca Beta'", res_form.context['form'].errors['recinto_cria_1'][0])
