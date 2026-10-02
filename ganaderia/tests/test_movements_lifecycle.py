from django.test import TestCase
from datetime import date
from ganaderia.models import Explotacion, Finca, Animal, Parto
from ganaderia.services.animal_services import trasladar_animal, dar_de_baja_animal, registrar_parto


class MovementsLifecycleTestCase(TestCase):
    def setUp(self):
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Movimientos",
            codigo_rega="ES888888888888"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca 1"
        )

    def test_traslado_cebadero_seals_and_resets_date(self):
        """Traslado a CEBADERO sella fecha_entrada_cebadero. Retorno a PASTO la resetea a None."""
        animal = Animal.objects.create(
            crotal="0200",
            sexo="M",
            fecha_nacimiento=date(2022, 1, 1),
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        self.assertIsNone(animal.fecha_entrada_cebadero)

        # Traslado a CEBADERO
        trasladar_animal(animal, self.finca, "CEBADERO")
        animal.refresh_from_db()
        self.assertEqual(animal.sub_ubicacion, "CEBADERO")
        self.assertIsNotNone(animal.fecha_entrada_cebadero)
        self.assertEqual(animal.fecha_entrada_cebadero, date.today())

        # Retorno a PASTO
        trasladar_animal(animal, self.finca, "PASTO")
        animal.refresh_from_db()
        self.assertEqual(animal.sub_ubicacion, "PASTO")
        self.assertIsNone(animal.fecha_entrada_cebadero)

    def test_mother_baja_preserves_offspring(self):
        """Baja de madre: La madre pasa a BAJA, pero las crías permanecen VIVAS e intactas."""
        madre = Animal.objects.create(
            crotal="0201",
            sexo="H",
            fecha_nacimiento=date(2017, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        res = registrar_parto(
            madre=madre,
            fecha_parto=date(2023, 1, 1),
            datos_cria={'crotal': '0202', 'sexo': 'M', 'raza': 'RETINTA'}
        )
        cria = res['cria']

        self.assertEqual(cria.estado_vital, 'VIVO')
        self.assertEqual(cria.madre.pk, madre.pk)

        # Dar de baja a la madre
        dar_de_baja_animal(madre, motivo="Venta")
        madre.refresh_from_db()
        cria.refresh_from_db()

        self.assertEqual(madre.estado_vital, 'BAJA')
        self.assertEqual(cria.estado_vital, 'VIVO')
        self.assertEqual(cria.madre.pk, madre.pk)
