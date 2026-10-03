from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from ganaderia.models import Explotacion, Finca, Animal, Parto, Incidencia, Ubicacion
from ganaderia.services.ai_assistant import procesar_comando_parto, _extraer_comando_parto_regex
from ganaderia.services.animal_services import registrar_parto


class AIAssistantTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación IA Test",
            codigo_rega="ES123456789012"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Pruebas"
        )
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="CEBADERO")

        # Madre adulta (> 18 meses)
        self.madre = Animal.objects.create(
            crotal="3014",
            sexo="H",
            raza="Retinta",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

    def test_extraccion_comando_regex_fallback(self):
        """1. Test unitario de extracción por fallback/regex en lenguaje natural."""
        texto = "La 3014 parió hoy ternera 5012 limusina"
        resultado = _extraer_comando_parto_regex(texto)
        self.assertEqual(resultado["intencion"], "REGISTRAR_PARTO")
        self.assertEqual(resultado["crotal_madre"], "3014")
        self.assertEqual(resultado["cria_crotal"], "5012")
        self.assertEqual(resultado["cria_sexo"], "H")
        self.assertEqual(resultado["cria_raza"], "Limusina")
        self.assertEqual(resultado["fecha_parto"], timezone.now().date().strftime("%Y-%m-%d"))

        # Test con macho y fecha ayer
        texto_macho = "Ayer parió la vaca 3014 un ternero con crotal 5013 en cebadero"
        resultado_macho = _extraer_comando_parto_regex(texto_macho)
        self.assertEqual(resultado_macho["cria_sexo"], "M")
        self.assertEqual(resultado_macho["cria_crotal"], "5013")
        self.assertEqual(resultado_macho["cria_recinto"], "CEBADERO")
        ayer = timezone.now().date() - timedelta(days=1)
        self.assertEqual(resultado_macho["fecha_parto"], ayer.strftime("%Y-%m-%d"))

    @override_settings(GEMINI_API_KEY="test-fake-key")
    @patch("ganaderia.services.ai_assistant.genai.Client")
    def test_extraccion_mock_gemini_llm(self, mock_client_class):
        """2. Test unitario de extracción mediante mock de Gemini LLM (SDK google-genai)."""
        mock_response = MagicMock()
        mock_response.text = '''{
            "intencion": "REGISTRAR_PARTO",
            "crotal_madre": "3014",
            "fecha_parto": "2026-10-02",
            "cria_crotal": "5012",
            "cria_sexo": "H",
            "cria_raza": "Limusina",
            "cria_recinto": "PASTO"
        }'''
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client

        resultado = procesar_comando_parto("La 3014 parió hoy ternera 5012 limusina")
        self.assertEqual(resultado["intencion"], "REGISTRAR_PARTO")
        self.assertEqual(resultado["crotal_madre"], "3014")
        self.assertEqual(resultado["cria_crotal"], "5012")
        self.assertEqual(resultado["cria_sexo"], "H")
        self.assertEqual(resultado["cria_raza"], "Limusina")
        self.assertEqual(resultado["fecha_parto"], "2026-10-02")

    def test_preview_detecta_conflicto_270_dias_sin_tocar_bd(self):
        """3. Test preview con conflicto < 270 días: devuelve alerta roja y NO modifica la base de datos."""
        # Registramos un parto hace 100 días para la vaca 3014
        hace_100_dias = timezone.now().date() - timedelta(days=100)
        registrar_parto(
            madre=self.madre,
            fecha_parto=hace_100_dias,
            crias=[{'crotal': '3333', 'sexo': 'M'}]
        )

        partos_iniciales = Parto.objects.count()
        animales_iniciales = Animal.objects.count()

        url_preview = reverse('asistente_preview')
        response = self.client.post(url_preview, {
            'texto': 'La 3014 parió hoy ternera 5012 limusina'
        })

        self.assertEqual(response.status_code, 200)
        # Debe contener el banner de conflicto normativo
        self.assertContains(response, "CONFLICTO NORMATIVO (Diputación)")
        self.assertContains(response, "Alerta Roja oficial")
        self.assertContains(response, "Confirmar con Alerta Roja")

        # Verificar que la base de datos NO ha sido alterada
        self.assertEqual(Parto.objects.count(), partos_iniciales)
        self.assertEqual(Animal.objects.count(), animales_iniciales)
        self.assertFalse(Animal.objects.filter(crotal="5012").exists())

    def test_preview_bloqueo_madre_inexistente_o_macho(self):
        """4. Test preview: madre que no existe o es macho debe arrojar error bloqueante."""
        macho = Animal.objects.create(
            crotal="9999",
            sexo="M",
            raza="Retinta",
            fecha_nacimiento=date(2020, 1, 1),
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        url_preview = reverse('asistente_preview')

        # Intento con toro macho
        res_macho = self.client.post(url_preview, {'texto': 'El 9999 parió hoy ternera 5014'})
        self.assertContains(res_macho, "es un Macho y no puede parir")
        self.assertNotContains(res_macho, "Confirmar y Guardar")

        # Intento con crotal inexistente
        res_no = self.client.post(url_preview, {'texto': 'La 8888 parió hoy ternera 5014'})
        self.assertContains(res_no, "No existe ninguna madre con el crotal #8888")
        self.assertNotContains(res_no, "Confirmar y Guardar")

    def test_preview_crotal_baja_muestra_alerta_amarilla(self):
        """5. Test preview: crotal de cría perteneciente a un animal en baja activa advertencia amarilla."""
        Animal.objects.create(
            crotal="5012",
            sexo="H",
            fecha_nacimiento=date(2021, 1, 1),
            estado_vital="BAJA",
            fecha_baja=date(2023, 1, 1),
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

        url_preview = reverse('asistente_preview')
        response = self.client.post(url_preview, {
            'texto': 'La 3014 parió hoy ternera 5012 retinta'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "perteneció históricamente a un animal en baja")

    def test_ejecucion_confirmada_crea_parto_y_cria_atomicamente(self):
        """6. Test de ejecución confirmada: POST a /asistente/ejecutar/ persiste parto y cría."""
        hoy_str = timezone.now().date().strftime("%Y-%m-%d")
        url_ejecutar = reverse('asistente_ejecutar')

        response = self.client.post(url_ejecutar, {
            'crotal_madre': '3014',
            'fecha_parto': hoy_str,
            'cria_crotal': '5012',
            'cria_sexo': 'H',
            'cria_raza': 'Limusina',
            'cria_recinto': 'PASTO',
            'forzar': '0',
        })

        # Redirige a la ficha del nuevo animal creado
        self.assertEqual(response.status_code, 302)
        self.assertIn('/animales/5012/', response.url)

        # Comprobar base de datos
        cria = Animal.objects.get(crotal="5012")
        self.assertEqual(cria.sexo, "H")
        self.assertEqual(cria.raza, "Limusina")
        self.assertEqual(cria.madre, self.madre)
        self.assertEqual(cria.estado_vital, "VIVO")

        parto = Parto.objects.get(cria=cria)
        self.assertEqual(parto.madre, self.madre)
        self.assertFalse(parto.alerta_intervalo)

    def test_ejecucion_con_alerta_roja_persiste_alerta_e_incidencia(self):
        """7. Test de ejecución con forzar=1: persiste alerta_intervalo=True e Incidencia ROJO."""
        hace_50_dias = timezone.now().date() - timedelta(days=50)
        registrar_parto(
            madre=self.madre,
            fecha_parto=hace_50_dias,
            crias=[{'crotal': '4001', 'sexo': 'M'}]
        )

        hoy_str = timezone.now().date().strftime("%Y-%m-%d")
        url_ejecutar = reverse('asistente_ejecutar')

        response = self.client.post(url_ejecutar, {
            'crotal_madre': '3014',
            'fecha_parto': hoy_str,
            'cria_crotal': '4002',
            'cria_sexo': 'M',
            'cria_raza': 'Retinta',
            'cria_recinto': 'PASTO',
            'forzar': '1',
        })
        self.assertEqual(response.status_code, 302)

        cria = Animal.objects.get(crotal="4002")
        parto = Parto.objects.get(cria=cria)
        self.assertTrue(parto.alerta_intervalo)

        incidencia = Incidencia.objects.filter(parto=parto, tipo='ROJO').first()
        self.assertIsNotNone(incidencia)
        self.assertFalse(incidencia.resuelta)

    def test_csrf_enforced_requests_succeed(self):
        """8. Test de protección CSRF: peticiones con enforce_csrf_checks=True no son bloqueadas con 403."""
        csrf_client = Client(enforce_csrf_checks=True)
        url_preview = reverse('asistente_preview')
        response = csrf_client.post(url_preview, {
            'texto': 'La 3014 parió hoy ternera 7777 retinta'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '7777')

