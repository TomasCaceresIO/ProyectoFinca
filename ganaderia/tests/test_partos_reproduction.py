from django.test import TestCase, Client
from django.urls import reverse
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

    def test_270_days_rule(self):
        """Parto a 269 días activa alerta_intervalo=True. Parto a 270 días lo deja en False."""
        p1_date = date(2023, 1, 1)
        registrar_parto(self.madre, p1_date)

        # Parto a 269 días (2023-09-27)
        p269_date = p1_date + timedelta(days=269)
        val_269 = validar_intervalo_parto(self.madre, p269_date)
        self.assertFalse(val_269['valido'])
        self.assertEqual(val_269['alerta'], 'ROJO')

        res_269 = registrar_parto(self.madre, p269_date, forzar=True)
        self.assertTrue(res_269['parto'].alerta_intervalo)

        # Parto a 270 días desde p269 (269 + 270 = 539 días desde p1)
        p270_date = p269_date + timedelta(days=270)
        val_270 = validar_intervalo_parto(self.madre, p270_date)
        self.assertTrue(val_270['valido'])
        self.assertIsNone(val_270['alerta'])

        res_270 = registrar_parto(self.madre, p270_date)
        self.assertFalse(res_270['parto'].alerta_intervalo)

    def test_retrospective_bidirectional_collision(self):
        """Modificación/inserción retrospectiva: comprueba colisión bi-direccional con el parto posterior o anterior."""
        p1 = registrar_parto(self.madre, date(2024, 1, 1))['parto']
        p2 = registrar_parto(self.madre, date(2025, 6, 1))['parto']

        # Insertar parto intermedio en 2024-11-01:
        # Respecto a p1 (2024-01-01): 305 días (>= 270)
        # Respecto a p2 (2025-06-01): 212 días (< 270) -> Colisiona con posterior!
        val_retro = validar_intervalo_parto(self.madre, date(2024, 11, 1))
        self.assertFalse(val_retro['valido'])
        self.assertEqual(val_retro['alerta'], 'ROJO')
        self.assertIsNotNone(val_retro['parto_posterior'])

        p_retro = registrar_parto(self.madre, date(2024, 11, 1), forzar=True)['parto']
        self.assertTrue(p_retro.alerta_intervalo)

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
