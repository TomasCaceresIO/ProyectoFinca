from datetime import date
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from ganaderia.models import Explotacion, Finca, Animal, Ubicacion
from ganaderia.services.ai_assistant import procesar_importacion_lote, _extraer_lote_regex


class LoteImportAndFundadoresTestCase(TestCase):
    """Pruebas para animales fundadores e importación masiva de censo (PDF / Voz / Texto)."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Fundadores",
            codigo_rega="ES111222333444"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Montealto"
        )
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="CEBADERO")

    def test_alta_animal_sin_madre_como_fundador(self):
        """
        1. Verificar que crear un animal sin madre asigna madre = None
        y se persiste con éxito sin lanzar excepciones.
        """
        animal = Animal.objects.create(
            crotal="8001",
            sexo="H",
            raza="Retinta",
            fecha_nacimiento=date(2021, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO",
            madre=None,
        )
        self.assertIsNotNone(animal.pk)
        self.assertIsNone(animal.madre)
        
        # Comprobar renderizado de la ficha del animal
        res = self.client.get(reverse('animal_detail', kwargs={'crotal': '8001'}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Fundador / Sin madre registrada")

    def test_ai_import_lote_texto_mixto(self):
        """
        2. Simular comando de voz/texto con 3 animales (uno sin madre) ->
        Comprobar que el preview extrae los 3 registros correctamente.
        """
        texto = "Añade al pasto en Finca Montealto los animales 4001 macho limusín, 4002 hembra charolesa y 4003 hembra sin madre nacidos en 2024"
        
        # Probar el parser simulado
        resultado = _extraer_lote_regex(texto, fincas_disponibles=["Finca Montealto"])
        self.assertEqual(resultado["intencion"], "IMPORTAR_LOTE")
        self.assertEqual(resultado["finca_nombre"], "Finca Montealto")
        self.assertEqual(len(resultado["animales"]), 3)

        crotales = [a["crotal"] for a in resultado["animales"]]
        self.assertIn("4001", crotales)
        self.assertIn("4002", crotales)
        self.assertIn("4003", crotales)

        # Probar la vista /asistente/lote-preview/
        res_view = self.client.post(reverse('asistente_lote_preview'), {'texto': texto})
        self.assertEqual(res_view.status_code, 200)
        self.assertContains(res_view, "4001")
        self.assertContains(res_view, "4002")
        self.assertContains(res_view, "4003")
        self.assertContains(res_view, "Fundador / Sin madre registrada")

    def test_ai_import_lote_bloquea_crotal_duplicado(self):
        """
        3. Si uno de los crotales del PDF/texto ya existe en estado VIVO,
        el preview lo marca como inválido y deshabilitado.
        """
        # Crear animal 4001 previamente
        Animal.objects.create(
            crotal="4001",
            sexo="M",
            raza="Limusina",
            fecha_nacimiento=date(2022, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

        texto = "Añadir animales 4001 macho y 4002 hembra en 2024"
        res = self.client.post(reverse('asistente_lote_preview'), {'texto': texto})
        self.assertEqual(res.status_code, 200)

        # 4001 debe figurar con error de duplicado y checkbox deshabilitado
        self.assertContains(res, "Crotal duplicado en censo activo")
        self.assertContains(res, 'disabled')

        # 4002 debe figurar como válido
        self.assertContains(res, "4002")
        self.assertContains(res, "Válido")

    def test_ai_import_lote_detecta_duplicidad_interna_del_propio_lote(self):
        """
        Verifica que al procesar un lote con un crotal repetido internamente (ej. crotal 1001 en fila 01
        y crotal 1001 repetido más adelante), el primero es válido y el segundo se marca en rojo
        por duplicidad interna ('Crotal repetido dentro del mismo lote') con checkbox deshabilitado.
        """
        with patch('ganaderia.views.procesar_importacion_lote') as mock_proc:
            mock_proc.return_value = {
                'intencion': 'IMPORTAR_LOTE',
                'finca_nombre': 'Finca Montealto',
                'animales': [
                    {'crotal': '1001', 'sexo': 'H', 'raza': 'Limusina', 'fecha_nacimiento': '2018-05-10', 'crotal_madre': None, 'sub_ubicacion': 'PASTO'},
                    {'crotal': '1002', 'sexo': 'M', 'raza': 'Retinta', 'fecha_nacimiento': '2020-03-15', 'crotal_madre': '1001', 'sub_ubicacion': 'PASTO'},
                    {'crotal': '1001', 'sexo': 'H', 'raza': 'Limusina', 'fecha_nacimiento': '2025-10-10', 'crotal_madre': '1002', 'sub_ubicacion': 'PASTO'},
                ],
                'error': None
            }

            res = self.client.post(reverse('asistente_lote_preview'), {'texto': 'lote simulado con duplicado'})
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "Crotal repetido dentro del mismo lote")

            animales_eval = res.context['animales']
            self.assertEqual(len(animales_eval), 3)

            # Primer 1001: Válido
            self.assertTrue(animales_eval[0]['es_valido'])
            self.assertEqual(animales_eval[0]['crotal'], '1001')
            self.assertIsNone(animales_eval[0]['error'])

            # Segundo 1002: Válido
            self.assertTrue(animales_eval[1]['es_valido'])

            # Tercero (repetido 1001): Inválido
            self.assertFalse(animales_eval[2]['es_valido'])
            self.assertEqual(animales_eval[2]['crotal'], '1001')
            self.assertEqual(animales_eval[2]['error'], "Crotal repetido dentro del mismo lote")

    def test_ejecucion_lote_atomica(self):
        """
        4. Confirmar la importación de 5 animales simultáneos y verificar
        que todos se persisten en la finca y recinto indicados.
        """
        data = {
            'finca_id': self.finca.pk,
            'recinto_global': 'CEBADERO',
            'seleccionados': ['0', '1', '2', '3', '4'],
            'crotal_0': '9001', 'sexo_0': 'H', 'raza_0': 'Limusina', 'fecha_nacimiento_0': '2023-01-01', 'crotal_madre_0': '',
            'crotal_1': '9002', 'sexo_1': 'M', 'raza_1': 'Limusina', 'fecha_nacimiento_1': '2023-02-01', 'crotal_madre_1': '',
            'crotal_2': '9003', 'sexo_2': 'H', 'raza_2': 'Retinta', 'fecha_nacimiento_2': '2023-03-01', 'crotal_madre_2': '',
            'crotal_3': '9004', 'sexo_3': 'M', 'raza_3': 'Charolesa', 'fecha_nacimiento_3': '2023-04-01', 'crotal_madre_3': '',
            'crotal_4': '9005', 'sexo_4': 'H', 'raza_4': 'Limusina', 'fecha_nacimiento_4': '2023-05-01', 'crotal_madre_4': '',
        }

        res = self.client.post(reverse('asistente_lote_ejecutar'), data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse('home'))

        self.assertEqual(Animal.objects.filter(finca=self.finca).count(), 5)
        for c in ['9001', '9002', '9003', '9004', '9005']:
            a = Animal.objects.get(crotal=c)
            self.assertEqual(a.sub_ubicacion, 'CEBADERO')
            self.assertIsNotNone(a.fecha_entrada_cebadero)
            self.assertIsNone(a.madre)
