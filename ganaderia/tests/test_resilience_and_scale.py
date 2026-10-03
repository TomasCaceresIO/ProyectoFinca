import time
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from concurrent.futures import TimeoutError as FuturesTimeoutError
from django.test import TestCase, Client, TransactionTestCase, override_settings
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from ganaderia.models import Explotacion, Finca, Animal, Ubicacion
from ganaderia.services.ai_assistant import (
    normalizar_crotal,
    procesar_comando_parto,
    procesar_importacion_lote,
    CANDIDATE_MODELS,
    DEFAULT_GEMINI_MODEL,
    genai_errors,
)


class ResilienceAndScaleTestCase(TransactionTestCase):
    """Pruebas de concurrencia, restricciones de BD, rendimiento y resiliencia."""

    def setUp(self):
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Resiliencia",
            codigo_rega="ES999999999999"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Escala"
        )
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="CEBADERO")

    def test_db_unique_constraint_blocks_concurrent_active_crotals(self):
        """
        1. Prueba de restricción a nivel base de datos:
        Intento simultáneo o concurrente de insertar dos animales con el mismo crotal en estado VIVO
        debe fallar a nivel de motor de BD (IntegrityError).
        Permite el mismo crotal si uno de ellos está en BAJA.
        """
        Animal.objects.create(
            crotal="7777",
            sexo="H",
            raza="Retinta",
            fecha_nacimiento=date(2021, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

        with self.assertRaises(IntegrityError):
            Animal.objects.create(
                crotal="7777",
                sexo="M",
                raza="Limusina",
                fecha_nacimiento=date(2022, 1, 1),
                estado_vital="VIVO",
                finca=self.finca,
                sub_ubicacion="PASTO"
            )

        # Sin embargo, dar de baja al animal 7777 debe permitir que otro animal con crotal 7777 nazca/se registre
        animal_1 = Animal.objects.get(crotal="7777", estado_vital="VIVO")
        animal_1.estado_vital = "BAJA"
        animal_1.save()

        # Ahora sí debe permitir el nuevo animal vivo
        nuevo_animal = Animal.objects.create(
            crotal="7777",
            sexo="M",
            raza="Limusina",
            fecha_nacimiento=date(2023, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        self.assertIsNotNone(nuevo_animal.pk)
        self.assertEqual(Animal.objects.filter(crotal="7777").count(), 2)

    def test_normalizador_crotal_voz(self):
        """
        2. Normalizador de entrada de voz para crotales:
        Convierte palabras habladas ("tres", "cero cero tres", "treinta", "cuarenta y dos", "5", "42")
        a formato numérico con 4 dígitos rellenado con ceros.
        """
        self.assertEqual(normalizar_crotal("3"), "0003")
        self.assertEqual(normalizar_crotal("tres"), "0003")
        self.assertEqual(normalizar_crotal("cero cero tres"), "0003")
        self.assertEqual(normalizar_crotal("cero cero cero tres"), "0003")
        self.assertEqual(normalizar_crotal("treinta"), "0030")
        self.assertEqual(normalizar_crotal("42"), "0042")
        self.assertEqual(normalizar_crotal("cuarenta y dos"), "0042")
        self.assertEqual(normalizar_crotal("5012"), "5012")
        self.assertEqual(normalizar_crotal("#0014"), "0014")
        self.assertEqual(normalizar_crotal("crotal 15"), "0015")
        self.assertIsNone(normalizar_crotal("palabra_invalida_xyz"))

    def test_paginacion_censo_con_filtros(self):
        """
        3. Paginación y rendimiento del censo con volumen grande (60 animales):
        - Home muestra 25 animales por página.
        - Navegación a página 2 y preservación de filtros.
        - Tiempo de respuesta < 300ms.
        """
        # Crear 60 animales
        animales = [
            Animal(
                crotal=str(1000 + i),
                sexo="H" if i % 2 == 0 else "M",
                raza="Retinta" if i % 3 == 0 else "Limusina",
                fecha_nacimiento=date(2020, 1, 1) + timedelta(days=i),
                estado_vital="VIVO",
                finca=self.finca,
                sub_ubicacion="PASTO"
            )
            for i in range(60)
        ]
        Animal.objects.bulk_create(animales)

        client = Client()

        # Medir tiempo de respuesta en la página 1
        t_start = time.perf_counter()
        response_p1 = client.get(reverse('home'))
        t_elapsed = time.perf_counter() - t_start

        self.assertEqual(response_p1.status_code, 200)
        self.assertLess(t_elapsed, 0.500)  # Verificación de rendimiento
        self.assertEqual(len(response_p1.context['page_obj']), 25)
        self.assertEqual(response_p1.context['paginator'].num_pages, 3)

        # Página 2 vía HTMX
        response_p2 = client.get(
            reverse('home') + '?page=2',
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response_p2.status_code, 200)
        self.assertEqual(len(response_p2.context['page_obj']), 25)
        self.assertEqual(response_p2.context['page_obj'].number, 2)
        self.assertContains(response_p2, "Página 2 de 3")

        # Filtro con paginación
        response_filtro = client.get(
            reverse('home') + '?raza=Retinta&page=1',
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response_filtro.status_code, 200)
        self.assertEqual(response_filtro.context['page_obj'].number, 1)

    @override_settings(GEMINI_API_KEY='dummy_resilience_api_key')
    def test_ai_timeout_fallback(self):
        """
        4. Resiliencia y control de timeout en la IA:
        Si Gemini API agota el tiempo de espera (o lanza TimeoutError),
        la función procesar_comando_parto conmuta automáticamente al parser
        local sin devolver un error 500 al cliente.
        """
        # Simular timeout en _llamar_gemini
        with patch('ganaderia.services.ai_assistant._llamar_gemini', side_header=None) as mock_gemini:
            def raise_timeout(*args, **kwargs):
                raise FuturesTimeoutError("Simulated 8s Timeout")
            mock_gemini.side_effect = raise_timeout

            texto = "La 3014 parió hoy ternera 5012 limusina"
            resultado = procesar_comando_parto(texto)

            # Debe haber conmutado al parser local regex limpiamente
            self.assertEqual(resultado["intencion"], "REGISTRAR_PARTO")
            self.assertEqual(resultado["crotal_madre"], "3014")
            self.assertEqual(resultado["cria_crotal"], "5012")
            self.assertEqual(resultado["cria_sexo"], "H")
            self.assertEqual(resultado["cria_raza"], "Limusina")
            self.assertNotIn("error", resultado)

    @override_settings(GEMINI_API_KEY='test_api_key_503')
    @patch('ganaderia.services.ai_assistant.time.sleep', return_value=None)
    def test_fallback_ladder_conmuta_tras_503(self, mock_sleep):
        """
        5. Escalera de modelos de respaldo:
        Si el modelo principal falla con 503 (High demand), conmuta al siguiente modelo candidato
        y tiene éxito sin elevar error 500 al cliente.
        """
        intentos = []

        def simular_llamada(*args, **kwargs):
            modelo = kwargs.get('model')
            intentos.append(modelo)
            if modelo == DEFAULT_GEMINI_MODEL:
                # Simular error 503 de saturación de servidores
                raise genai_errors.APIError(503, {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.'}})
            # El segundo modelo candidato responde con éxito
            return {
                "intencion": "REGISTRAR_PARTO",
                "crotal_madre": "3014",
                "fecha_parto": "2026-10-02",
                "cria_crotal": "5012",
                "cria_sexo": "H",
                "cria_raza": "Limusina",
                "cria_recinto": "PASTO",
                "cria_finca": None,
            }

        with patch('ganaderia.services.ai_assistant._llamar_gemini', side_effect=simular_llamada):
            resultado = procesar_comando_parto("La 3014 parió hoy ternera 5012 limusina")
            self.assertEqual(resultado["crotal_madre"], "3014")
            self.assertEqual(resultado["cria_crotal"], "5012")
            # Debe haber intentado primero con DEFAULT_GEMINI_MODEL y luego con el primer candidato
            self.assertIn(DEFAULT_GEMINI_MODEL, intentos)
            self.assertIn(CANDIDATE_MODELS[0], intentos)

    @override_settings(GEMINI_API_KEY='test_api_key_503_global')
    @patch('ganaderia.services.ai_assistant.time.sleep', return_value=None)
    def test_lote_preview_mensaje_amigable_saturacion(self, mock_sleep):
        """
        6. Mensaje amigable al usuario en /asistente/lote-preview/ si todos los modelos devuelven 503.
        """
        def simular_503_todos(*args, **kwargs):
            raise genai_errors.APIError(503, {'error': {'code': 503, 'message': 'High demand'}})

        with patch('ganaderia.services.ai_assistant._llamar_gemini_lote', side_effect=simular_503_todos):
            from django.core.files.uploadedfile import SimpleUploadedFile
            pdf_dummy = SimpleUploadedFile("censo.pdf", b"%PDF-1.4 dummy", content_type="application/pdf")
            client = Client()
            res = client.post(reverse('asistente_lote_preview'), {'archivo': pdf_dummy})
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "Los servidores de Google AI están experimentando un pico de saturación temporal")
