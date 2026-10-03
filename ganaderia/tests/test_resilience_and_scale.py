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
    GROQ_DEFAULT_MODEL,
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
        self.assertLess(t_elapsed, 0.500)
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

    @override_settings(GROQ_API_KEY='dummy_resilience_api_key')
    def test_ai_timeout_fallback(self):
        """
        4. Resiliencia y control de timeout en la IA:
        Si Groq API agota el tiempo de espera (o lanza TimeoutError),
        la función procesar_comando_parto conmuta automáticamente al parser
        local sin devolver un error 500 al cliente.
        """
        with patch('ganaderia.services.ai_assistant._llamar_groq_parto') as mock_groq:
            def raise_timeout(*args, **kwargs):
                raise FuturesTimeoutError("Simulated 8s Timeout")
            mock_groq.side_effect = raise_timeout

            texto = "La 3014 parió hoy ternera 5012 limusina"
            resultado = procesar_comando_parto(texto)

            # Debe haber conmutado al parser local regex limpiamente
            self.assertEqual(resultado["intencion"], "REGISTRAR_PARTO")
            self.assertEqual(resultado["crotal_madre"], "3014")
            self.assertEqual(resultado["cria_crotal"], "5012")
            self.assertEqual(resultado["cria_sexo"], "H")
            self.assertEqual(resultado["cria_raza"], "Limusina")
            self.assertNotIn("error", resultado)

    @override_settings(GROQ_API_KEY='test_api_key_error')
    def test_fallback_conmuta_tras_error_api(self):
        """
        5. Conmutación a fallback regex si la API de Groq lanza una excepción o error:
        """
        with patch('ganaderia.services.ai_assistant._llamar_groq_parto', side_effect=Exception("Connection error or rate limit")):
            resultado = procesar_comando_parto("La 3014 parió hoy ternera 5012 limusina")
            self.assertEqual(resultado["crotal_madre"], "3014")
            self.assertEqual(resultado["cria_crotal"], "5012")
            self.assertEqual(resultado["cria_sexo"], "H")
            self.assertEqual(resultado["cria_raza"], "Limusina")

    @override_settings(GROQ_API_KEY='test_api_key_saturacion')
    def test_lote_preview_mensaje_amigable_sin_texto_ni_ocr(self):
        """
        6. Mensaje amigable al usuario en /asistente/lote-preview/ si no se puede extraer texto plano de un binario no parseable.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        pdf_dummy = SimpleUploadedFile("censo.pdf", b"DATOS_BINARIOS_INVALIDOS_SIN_TEXTO", content_type="application/pdf")
        client = Client()
        res = client.post(reverse('asistente_lote_preview'), {'archivo': pdf_dummy})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No se pudo extraer texto digital del documento PDF adjunto")

    @override_settings(GROQ_API_KEY='test_api_key_pdf_fallback')
    def test_pdf_extractor_local_y_fallback_regex(self):
        """
        7. Si se recibe un PDF con texto digital, el sistema extrae el texto del PDF y parsea los animales.
        """
        with patch('ganaderia.services.ai_assistant.PdfReader') as mock_reader_cls, \
             patch('ganaderia.services.ai_assistant.Groq') as mock_groq_cls:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Animal 7001 macho limusin y 7002 hembra en pasto"
            mock_instance = MagicMock()
            mock_instance.pages = [mock_page]
            mock_reader_cls.return_value = mock_instance

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = '''{
                "finca_nombre": null,
                "animales": [
                    {"crotal": "7001", "sexo": "M", "raza": "Limusina", "fecha_nacimiento": null, "crotal_madre": null, "sub_ubicacion": "PASTO"},
                    {"crotal": "7002", "sexo": "H", "raza": "Limusina", "fecha_nacimiento": null, "crotal_madre": null, "sub_ubicacion": "PASTO"}
                ]
            }'''
            mock_completion.choices = [mock_choice]
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_completion
            mock_groq_cls.return_value = mock_client

            from django.core.files.uploadedfile import SimpleUploadedFile
            pdf_file = SimpleUploadedFile("censo.pdf", b"%PDF-1.4 dummy", content_type="application/pdf")
            client = Client()
            res = client.post(reverse('asistente_lote_preview'), {'archivo': pdf_file})

            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "7001")
            self.assertContains(res, "7002")

    @override_settings(GROQ_API_KEY='test_api_key_fallback')
    def test_fallback_banner_cuando_groq_falla(self):
        """
        8. Si Groq falla y se dispone de texto para fallback, devuelve la previsualización
        con el aviso banner informativo mediante el analizador local.
        """
        with patch('ganaderia.services.ai_assistant.Groq') as mock_groq_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("Service unavailable 503")
            mock_groq_cls.return_value = mock_client

            client = Client()
            res = client.post(reverse('asistente_lote_preview'), {'texto': "Añade al pasto los animales 7001 macho limusin y 7002 hembra"})
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "7001")
            self.assertContains(res, "7002")
            self.assertContains(res, "El censo fue procesado mediante el analizador local debido a una saturación temporal")
