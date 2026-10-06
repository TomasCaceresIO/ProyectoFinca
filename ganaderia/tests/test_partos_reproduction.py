from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from datetime import date, timedelta
from ganaderia.models import Explotacion, Finca, Animal, Parto
from ganaderia.services.animal_services import registrar_parto, validar_intervalo_parto, actualizar_parto


class PartosReproductionTestCase(TestCase):
    def setUp(self):
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Repro",
            codigo_rega="ES999999999999"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Repro"
        )
        self.madre = Animal.objects.create(
            crotal="0100",
            sexo="H",
            fecha_nacimiento=date(2018, 1, 1),
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)

    def test_270_days_rule(self):
        """Parto a 269 días activa alerta_intervalo=True. Parto a 270 días lo deja en False."""
        p1_date = date(2023, 1, 1)
        registrar_parto(self.madre, p1_date, crias=[{'crotal': '0801', 'sexo': 'M'}])

        p269_date = p1_date + timedelta(days=269)
        val_269 = validar_intervalo_parto(self.madre, p269_date)
        self.assertFalse(val_269['valido'])
        self.assertEqual(val_269['alerta'], 'ROJO')

        res_269 = registrar_parto(self.madre, p269_date, forzar=True, crias=[{'crotal': '0802', 'sexo': 'H'}])
        self.assertTrue(res_269['parto'].alerta_intervalo)

        p270_date = p269_date + timedelta(days=270)
        val_270 = validar_intervalo_parto(self.madre, p270_date)
        self.assertTrue(val_270['valido'])
        self.assertIsNone(val_270['alerta'])

        res_270 = registrar_parto(self.madre, p270_date, crias=[{'crotal': '0803', 'sexo': 'M'}])
        self.assertFalse(res_270['parto'].alerta_intervalo)

    def test_retrospective_bidirectional_collision(self):
        """Modificación/inserción retrospectiva: comprueba colisión bi-direccional con el parto posterior o anterior."""
        p1 = registrar_parto(self.madre, date(2024, 1, 1), crias=[{'crotal': '0804', 'sexo': 'M'}])['parto']
        p2 = registrar_parto(self.madre, date(2025, 6, 1), crias=[{'crotal': '0805', 'sexo': 'H'}])['parto']

        val_retro = validar_intervalo_parto(self.madre, date(2024, 11, 1))
        self.assertFalse(val_retro['valido'])
        self.assertEqual(val_retro['alerta'], 'ROJO')
        self.assertIsNotNone(val_retro['parto_posterior'])

        p_retro = registrar_parto(self.madre, date(2024, 11, 1), forzar=True, crias=[{'crotal': '0806', 'sexo': 'M'}])['parto']
        self.assertTrue(p_retro.alerta_intervalo)

    def test_registrar_parto_zero_crias_fails(self):
        """Intentar registrar un parto con 0 crías debe fallar por validación."""
        with self.assertRaises(ValidationError):
            registrar_parto(self.madre, date(2024, 1, 1), crias=[])

    def test_registrar_parto_one_cria_success(self):
        """Registrar parto con 1 cría debe ser exitoso."""
        res = registrar_parto(self.madre, date(2024, 1, 1), crias=[{'crotal': '0101', 'sexo': 'M'}])
        self.assertIsNotNone(res['parto'])
        self.assertIsNotNone(res['cria'])
        self.assertIsNone(res['cria2'])
        self.assertEqual(res['cria'].crotal, '0101')

    def test_registrar_parto_two_crias_twin_success(self):
        """Registrar parto con 2 crías (gemelar) -> Ambas crías deben quedar registradas y vinculadas."""
        crias_input = [
            {'crotal': '0102', 'sexo': 'M'},
            {'crotal': '0103', 'sexo': 'H'}
        ]
        res = registrar_parto(self.madre, date(2024, 1, 1), crias=crias_input)
        self.assertIsNotNone(res['parto'])
        self.assertIsNotNone(res['cria'])
        self.assertIsNotNone(res['cria2'])
        self.assertEqual(res['cria'].crotal, '0102')
        self.assertEqual(res['cria2'].crotal, '0103')
        self.assertEqual(res['parto'].cria.crotal, '0102')
        self.assertEqual(res['parto'].cria2.crotal, '0103')

    def test_registrar_parto_three_crias_fails(self):
        """Intentar registrar un parto con 3 crías debe fallar por validación."""
        crias_input = [
            {'crotal': '0104', 'sexo': 'M'},
            {'crotal': '0105', 'sexo': 'H'},
            {'crotal': '0106', 'sexo': 'M'}
        ]
        with self.assertRaises(ValidationError):
            registrar_parto(self.madre, date(2024, 1, 1), crias=crias_input)

    def test_parto_sin_cria_rendering(self):
        """Parto sin cría (cria=None): verificar que la ficha del animal renderiza sin lanzar AttributeError."""
        Parto.objects.create(
            madre=self.madre,
            fecha_parto=date(2023, 5, 10),
            cria=None,
            alerta_intervalo=False
        )
        url = reverse('animal_detail', kwargs={'crotal': self.madre.crotal})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin cría censada")

    def test_actualizar_parto_recalculates_alerts_and_resolves_incidencias(self):
        """Actualizar fecha de parto a >= 270d recalcula alerta_intervalo a False y marca Incidencia como resuelta."""
        res1 = registrar_parto(self.madre, date(2023, 1, 1), crias=[{'crotal': '0807', 'sexo': 'M'}])
        res2 = registrar_parto(self.madre, date(2023, 5, 1), forzar=True, crias=[{'crotal': '0808', 'sexo': 'H'}])
        p2 = res2['parto']

        self.assertTrue(p2.alerta_intervalo)
        from ganaderia.models import Incidencia
        inc = Incidencia.objects.get(parto=p2)
        self.assertFalse(inc.resuelta)

        # Actualizar fecha de p2 a 2023-11-01 (intervalo de 304 días > 270d)
        actualizar_parto(p2, date(2023, 11, 1))
        p2.refresh_from_db()
        inc.refresh_from_db()

        self.assertFalse(p2.alerta_intervalo)
        self.assertTrue(inc.resuelta)

