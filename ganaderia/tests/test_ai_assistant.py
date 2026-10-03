from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from ganaderia.models import Explotacion, Finca, Animal, Parto, Incidencia, Ubicacion
from ganaderia.services.ai_assistant import (
    procesar_comando_parto,
    procesar_importacion_lote,
    _extraer_comando_parto_regex,
    get_groq_client,
    GROQ_DEFAULT_MODEL,
)
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

    def test_extraccion_frase_fecha_pasada_explicita(self):
        """Test con frase con fecha pasada explícita: respeta fecha exacta y no confunde año con crotal."""
        texto = "La hembra 0001 parió una ternera limosina con crotal 0003 el día 10 de octubre de 2022"
        resultado = _extraer_comando_parto_regex(texto)
        self.assertEqual(resultado["crotal_madre"], "0001")
        self.assertEqual(resultado["cria_crotal"], "0003")
        self.assertEqual(resultado["fecha_parto"], "2022-10-10")
        self.assertEqual(resultado["cria_sexo"], "H")
        self.assertEqual(resultado["cria_raza"], "Limusina")

    def test_preview_sin_crotal_madre_devuelve_error_bloqueante(self):
        """Test sin crotal de madre: debe devolver error bloqueante sin tarjeta de previsualización/confirmación."""
        url_preview = reverse('asistente_preview')
        response = self.client.post(url_preview, {
            'texto': 'Parió una ternera con crotal 0003 hoy'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se especificó el crotal de la madre (debe ser un número de 4 dígitos)")
        self.assertFalse(response.context['puede_confirmar'])
        self.assertNotContains(response, "Confirmar y Guardar")

    def test_preview_sin_crotal_cria_devuelve_error_bloqueante(self):
        """Test sin crotal de cría: no debe tomar el año como crotal y debe devolver error bloqueante."""
        url_preview = reverse('asistente_preview')
        response = self.client.post(url_preview, {
            'texto': 'La hembra 3014 parió una ternera limosina el 10 de octubre de 2022'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se especificó el crotal de la cría (4 dígitos requeridos)")
        self.assertFalse(response.context['puede_confirmar'])
        self.assertNotContains(response, "Confirmar y Guardar")

    @override_settings(GROQ_API_KEY="test-fake-key")
    @patch("ganaderia.services.ai_assistant.Groq")
    def test_extraccion_mock_groq_llm(self, mock_groq_class):
        """2. Test unitario de extracción mediante mock de Groq LLM (llama-3.3-70b-versatile)."""
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '''{
            "intencion": "REGISTRAR_PARTO",
            "crotal_madre": "3014",
            "fecha_parto": "2026-10-02",
            "cria_crotal": "5012",
            "cria_sexo": "H",
            "cria_raza": "Limusina",
            "cria_recinto": "PASTO"
        }'''
        mock_completion.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion
        mock_groq_class.return_value = mock_client

        resultado = procesar_comando_parto("La 3014 parió hoy ternera 5012 limusina")
        self.assertEqual(resultado["intencion"], "REGISTRAR_PARTO")
        self.assertEqual(resultado["crotal_madre"], "3014")
        self.assertEqual(resultado["cria_crotal"], "5012")
        self.assertEqual(resultado["cria_sexo"], "H")
        self.assertEqual(resultado["cria_raza"], "Limusina")
        self.assertEqual(resultado["fecha_parto"], "2026-10-02")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], GROQ_DEFAULT_MODEL)
        self.assertEqual(GROQ_DEFAULT_MODEL, "openai/gpt-oss-120b")
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})

    @override_settings(GROQ_API_KEY="test-fake-key")
    @patch("ganaderia.services.ai_assistant.Groq")
    def test_fallback_cadena_modelos_404(self, mock_groq_class):
        """Verifica que si el primer modelo arroja 404 (NotFoundError), conmuta de inmediato al siguiente modelo."""
        from groq import NotFoundError
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '''{
            "finca_nombre": null,
            "animales": [
                {"crotal": "9901", "sexo": "H", "raza": "Limusina", "fecha_nacimiento": null, "crotal_madre": null, "sub_ubicacion": "PASTO"}
            ]
        }'''
        mock_completion.choices = [mock_choice]

        mock_client = MagicMock()
        def side_effect_models(*args, **kwargs):
            if kwargs.get("model") == "openai/gpt-oss-120b":
                raise NotFoundError("Model not found", response=MagicMock(status_code=404), body={"error": "model_not_found"})
            return mock_completion

        mock_client.chat.completions.create.side_effect = side_effect_models
        mock_groq_class.return_value = mock_client

        resultado = procesar_importacion_lote(texto="Animal 9901 hembra limusina")
        self.assertIsNone(resultado["error"])
        self.assertEqual(len(resultado["animales"]), 1)
        self.assertEqual(resultado["animales"][0]["crotal"], "9901")
        llamadas_modelos = [call.kwargs.get("model") for call in mock_client.chat.completions.create.call_args_list]
        self.assertIn("openai/gpt-oss-120b", llamadas_modelos)
        self.assertIn("openai/gpt-oss-20b", llamadas_modelos)

    @override_settings(GROQ_API_KEY="gsk_mock_test_token_not_real")
    @patch("ganaderia.services.ai_assistant.Groq")
    def test_client_initialization_groq(self, mock_groq_class):
        """Test unitario: autenticación con GROQ_API_KEY inicializa el cliente oficial sin error."""
        mock_client_instance = MagicMock()
        mock_groq_class.return_value = mock_client_instance

        client = get_groq_client()
        self.assertIsNotNone(client)
        mock_groq_class.assert_called_once_with(api_key="gsk_mock_test_token_not_real")

    @override_settings(GROQ_API_KEY="test-fake-key")
    @patch("ganaderia.services.ai_assistant.Groq")
    def test_lote_importacion_groq_llm_columnas_desordenadas(self, mock_groq_class):
        """Test importación de lote con Groq: 3 animales con columnas desordenadas, fechas históricas y fundadoras."""
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '''{
            "finca_nombre": "Finca Pruebas",
            "animales": [
                {
                    "crotal": "1001",
                    "sexo": "H",
                    "fecha_nacimiento": "2018-03-12",
                    "raza": "Limusina",
                    "crotal_madre": null,
                    "sub_ubicacion": "PASTO"
                },
                {
                    "crotal": "1002",
                    "sexo": "M",
                    "fecha_nacimiento": "2019-07-20",
                    "raza": "Retinta",
                    "crotal_madre": "1001",
                    "sub_ubicacion": "CEBADERO"
                },
                {
                    "crotal": "1003",
                    "sexo": "H",
                    "fecha_nacimiento": "2020-01-15",
                    "raza": "Charolesa",
                    "crotal_madre": null,
                    "sub_ubicacion": "PASTO"
                }
            ]
        }'''
        mock_completion.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion
        mock_groq_class.return_value = mock_client

        resultado = procesar_importacion_lote(texto="1001 H 2018-03-12 Limusina Fundadora | 1002 M Retinta 2019-07-20 Madre 1001 | 1003 H Charolesa 2020-01-15 -")
        self.assertIsNone(resultado["error"])
        self.assertEqual(len(resultado["animales"]), 3)

        a1 = resultado["animales"][0]
        self.assertEqual(a1["crotal"], "1001")
        self.assertEqual(a1["sexo"], "H")
        self.assertEqual(a1["fecha_nacimiento"], "2018-03-12")
        self.assertIsNone(a1["crotal_madre"])

        a2 = resultado["animales"][1]
        self.assertEqual(a2["crotal"], "1002")
        self.assertEqual(a2["sexo"], "M")
        self.assertEqual(a2["crotal_madre"], "1001")
        self.assertEqual(a2["sub_ubicacion"], "CEBADERO")

        a3 = resultado["animales"][2]
        self.assertEqual(a3["crotal"], "1003")
        self.assertEqual(a3["sexo"], "H")
        self.assertIsNone(a3["crotal_madre"])

    @override_settings(GROQ_API_KEY="test-fake-key")
    @patch("ganaderia.services.ai_assistant.PdfReader")
    @patch("ganaderia.services.ai_assistant.Groq")
    def test_lote_importacion_multipagina_chunking_pdf(self, mock_groq_class, mock_pdf_reader_cls):
        """Verifica que un PDF de múltiples páginas se procesa página por página sin truncamiento y consolida los animales."""
        mock_p1 = MagicMock()
        mock_p1.extract_text.return_value = "Página 1: 1001 H Limusina | 1002 M Retinta"
        mock_p2 = MagicMock()
        mock_p2.extract_text.return_value = "Página 2: 1003 H Charolesa | 1004 M Limusina"

        mock_reader_inst = MagicMock()
        mock_reader_inst.pages = [mock_p1, mock_p2]
        mock_pdf_reader_cls.return_value = mock_reader_inst

        resp_p1 = MagicMock()
        resp_p1.choices = [MagicMock(message=MagicMock(content='''{"animales": [
            {"crotal": "1001", "sexo": "H", "raza": "Limusina", "crotal_madre": null, "sub_ubicacion": "PASTO"},
            {"crotal": "1002", "sexo": "M", "raza": "Retinta", "crotal_madre": "1001", "sub_ubicacion": "PASTO"}
        ]}'''))]

        resp_p2 = MagicMock()
        resp_p2.choices = [MagicMock(message=MagicMock(content='''{"animales": [
            {"crotal": "1003", "sexo": "H", "raza": "Charolesa", "crotal_madre": null, "sub_ubicacion": "PASTO"},
            {"crotal": "1004", "sexo": "M", "raza": "Limusina", "crotal_madre": "1003", "sub_ubicacion": "PASTO"}
        ]}'''))]

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [resp_p1, resp_p2]
        mock_groq_class.return_value = mock_client

        resultado = procesar_importacion_lote(archivo_bytes=b"%PDF-1.4 dummy multi-page")
        self.assertIsNone(resultado["error"])
        self.assertEqual(len(resultado["animales"]), 4)
        crotales = [a["crotal"] for a in resultado["animales"]]
        self.assertEqual(crotales, ["1001", "1002", "1003", "1004"])
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

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

    def test_preview_crotal_baja_permitido_sin_alerta_amarilla(self):
        """5. Test preview: crotal de cría perteneciente a un animal en baja se permite limpiamente sin alerta amarilla."""
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
        self.assertNotContains(response, "Alerta Amarilla")
        self.assertNotContains(response, "perteneció históricamente a un animal en baja")
        self.assertTrue(response.context['puede_confirmar'])

    def test_preview_ajusta_fecha_futura_a_hoy_con_banner_informativo(self):
        """Test preview: enviar una fecha de mañana asigna hoy e incluye el mensaje informativo de ajuste."""
        url_preview = reverse('asistente_preview')
        response = self.client.post(url_preview, {
            'texto': 'La 3014 parió mañana ternera 5012 retinta'
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['fecha_ajustada_hoy'])
        hoy = timezone.now().date()
        self.assertEqual(response.context['fecha_parto'], hoy)
        self.assertEqual(response.context['fecha_solicitada_original'], hoy + timedelta(days=1))
        self.assertContains(response, "Fecha ajustada automáticamente")
        self.assertContains(response, "Indicaste una fecha posterior")
        self.assertTrue(response.context['puede_confirmar'])

    def test_dar_de_baja_madre_limpia_incidencias_y_alertas_parto(self):
        """Test baja madre: al dar de baja una hembra con parto en alerta roja, el parto limpia alerta_intervalo=False y desaparece de /incidencias/."""
        from ganaderia.services.animal_services import dar_de_baja_animal

        # Creamos parto con conflicto de intervalo (< 270d)
        p1 = registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 1, 1),
            crias=[{'crotal': '8801', 'sexo': 'M'}]
        )['parto']
        p2 = registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 5, 1),
            forzar=True,
            crias=[{'crotal': '8802', 'sexo': 'H'}]
        )['parto']

        self.assertTrue(p2.alerta_intervalo)
        self.assertTrue(Incidencia.objects.filter(parto=p2, resuelta=False).exists())

        url_incidencias = reverse('incidencias')
        res_inc = self.client.get(url_incidencias)
        self.assertContains(res_inc, f"Crotal {self.madre.crotal}")

        # Dar de baja a la madre
        dar_de_baja_animal(self.madre, motivo="Muerte natural")
        self.madre.refresh_from_db()
        p2.refresh_from_db()

        # Verificar que alerta_intervalo es False
        self.assertFalse(p2.alerta_intervalo)
        # Verificar que la incidencia se marcó como resuelta
        self.assertFalse(Incidencia.objects.filter(parto=p2, resuelta=False).exists())

        # Verificar que /incidencias/ ya no muestra la alerta
        res_inc_despues = self.client.get(url_incidencias)
        self.assertNotContains(res_inc_despues, f"Crotal {self.madre.crotal}")

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

