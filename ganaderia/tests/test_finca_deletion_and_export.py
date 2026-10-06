from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from datetime import date
from ganaderia.models import Explotacion, Finca, Animal, Ubicacion, Parto


class FincaDeletionAndExportTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Deletion & Export",
            codigo_rega="ES555555555555"
        )
        self.finca1 = Finca.objects.create(explotacion=self.explotacion, nombre="Finca Origen")
        self.finca2 = Finca.objects.create(explotacion=self.explotacion, nombre="Finca Destino")
        
        Ubicacion.objects.create(finca=self.finca1, tipo_ubicacion='PASTO')
        Ubicacion.objects.create(finca=self.finca2, tipo_ubicacion='PASTO')

        self.animal1 = Animal.objects.create(
            crotal="0501", sexo="H", fecha_nacimiento=date(2020, 1, 1),
            finca=self.finca1, sub_ubicacion="PASTO", estado_vital="VIVO"
        )
        self.animal2 = Animal.objects.create(
            crotal="0502", sexo="M", fecha_nacimiento=date(2021, 1, 1),
            finca=self.finca1, sub_ubicacion="PASTO", estado_vital="VIVO"
        )

    def test_finca_delete_opcion_a_traslado_total(self):
        """Test de eliminación de finca con traslado en bloque (Opción A)."""
        url = reverse('finca_delete', kwargs={'pk': self.finca1.pk})
        data = {
            'opcion': 'opcion_a',
            'finca_destino': self.finca2.pk,
        }
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse('finca_list'))

        # Finca 1 borrada
        self.assertFalse(Finca.objects.filter(pk=self.finca1.pk).exists())

        # Animales trasladados a Finca 2
        self.animal1.refresh_from_db()
        self.animal2.refresh_from_db()
        self.assertEqual(self.animal1.finca, self.finca2)
        self.assertEqual(self.animal2.finca, self.finca2)
        self.assertEqual(self.animal1.estado_vital, 'VIVO')
        self.assertEqual(self.animal2.estado_vital, 'VIVO')

    def test_finca_delete_opcion_b_baja_en_bloque(self):
        """Test de eliminación de finca con bajas en bloque (Opción B)."""
        url = reverse('finca_delete', kwargs={'pk': self.finca1.pk})
        data = {
            'opcion': 'opcion_b',
            'motivo_baja': 'Baja total por vaciado de finca',
        }
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse('finca_list'))

        self.assertFalse(Finca.objects.filter(pk=self.finca1.pk).exists())

        self.animal1.refresh_from_db()
        self.animal2.refresh_from_db()
        self.assertEqual(self.animal1.estado_vital, 'BAJA')
        self.assertEqual(self.animal2.estado_vital, 'BAJA')

    def test_finca_delete_opcion_c_granular(self):
        """Test de eliminación de finca con resolución granular por animal (Opción C)."""
        url = reverse('finca_delete', kwargs={'pk': self.finca1.pk})
        data = {
            'opcion': 'opcion_c',
            'finca_destino_c': self.finca2.pk,
            f'accion_{self.animal1.pk}': 'trasladar',
            f'accion_{self.animal2.pk}': 'baja',
        }
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse('finca_list'))

        # Finca 1 eliminada
        self.assertFalse(Finca.objects.filter(pk=self.finca1.pk).exists())

        # Animal 1 trasladado a Finca 2
        self.animal1.refresh_from_db()
        self.assertEqual(self.animal1.estado_vital, 'VIVO')
        self.assertEqual(self.animal1.finca, self.finca2)

        # Animal 2 registrado como BAJA
        self.animal2.refresh_from_db()
        self.assertEqual(self.animal2.estado_vital, 'BAJA')


    def test_exportar_csv_headers_and_filters(self):
        """Test del endpoint de exportación CSV verificando que las cabeceras y los filtros coinciden."""
        url = reverse('exportar_csv') + "?sexo=H"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('censo_ganadero_', res['Content-Disposition'])

        # Si es StreamingHttpResponse, consumir streaming_content
        if hasattr(res, 'streaming_content'):
            chunks = list(res.streaming_content)
            content = b''.join(c.encode('utf-8') if isinstance(c, str) else c for c in chunks).decode('utf-8-sig')
        else:
            content = res.content.decode('utf-8-sig')

        lines = [line for line in content.strip().splitlines() if line]
        self.assertTrue(len(lines) >= 2)
        self.assertIn('Crotal;Sexo;Raza', lines[0])

        # Solo debe incluir a animal1 (sexo='H') y no a animal2 (sexo='M')
        self.assertIn('0501', content)
        self.assertNotIn('0502', content)

    def test_exportar_excel_endpoint(self):
        """Test del endpoint de exportación Excel (.xlsx)."""
        url = reverse('exportar_excel')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_explotacion_edit(self):
        """Test de edición de nombre y Código REGA de la explotación."""
        url = reverse('explotacion_edit')
        data = {
            'nombre': 'Nueva Explotación Editada',
            'codigo_rega': 'ES999888777666'
        }
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 302)

        self.explotacion.refresh_from_db()
        self.assertEqual(self.explotacion.nombre, 'Nueva Explotación Editada')
        self.assertEqual(self.explotacion.codigo_rega, 'ES999888777666')
