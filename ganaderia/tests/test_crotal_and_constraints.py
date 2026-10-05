from django.test import TestCase
from django.db.utils import IntegrityError
from django.core.exceptions import ValidationError
from datetime import date
from ganaderia.models import Explotacion, Finca, Animal
from ganaderia.services.animal_services import validar_crotal, dar_de_baja_animal


class CrotalAndConstraintsTestCase(TestCase):
    def setUp(self):
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Test",
            codigo_rega="ES123456789012"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Test"
        )

    def test_crotal_exact_four_digits_validation(self):
        """Validar longitud exacta de 4 dígitos (rechazar letras, 3 y 5 dígitos)."""
        res_3d = validar_crotal("123")
        self.assertFalse(res_3d['valido'])
        self.assertTrue(res_3d['bloqueante'])

        res_5d = validar_crotal("12345")
        self.assertFalse(res_5d['valido'])
        self.assertTrue(res_5d['bloqueante'])

        res_letras = validar_crotal("A123")
        self.assertFalse(res_letras['valido'])
        self.assertTrue(res_letras['bloqueante'])

        res_ok = validar_crotal("0042")
        self.assertTrue(res_ok['valido'])
        self.assertFalse(res_ok['bloqueante'])

    def test_crotal_preserves_leading_zeros(self):
        """Comprobar que crotales como '0005' o '0042' conservan ceros a la izquierda."""
        animal = Animal.objects.create(
            crotal="0042",
            sexo="H",
            fecha_nacimiento=date(2020, 1, 1),
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        self.assertEqual(animal.crotal, "0042")
        
        # Buscar por texto exacto
        encontrado = Animal.objects.get(crotal="0042")
        self.assertEqual(encontrado.pk, animal.pk)

    def test_unique_constraint_crotal_vivo(self):
        """Verificar constraint de unicidad: error si se duplica un crotal en estado VIVO."""
        Animal.objects.create(
            crotal="0010",
            sexo="M",
            fecha_nacimiento=date(2021, 5, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        with self.assertRaises(IntegrityError):
            Animal.objects.create(
                crotal="0010",
                sexo="H",
                fecha_nacimiento=date(2022, 1, 1),
                estado_vital="VIVO",
                finca=self.finca,
                sub_ubicacion="PASTO"
            )

    def test_reusing_baja_crotal_allowed_without_alert(self):
        """Verificar que reutilizar un crotal de un animal en BAJA se permite directamente sin alertas."""
        a1 = Animal.objects.create(
            crotal="0099",
            sexo="H",
            fecha_nacimiento=date(2019, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        dar_de_baja_animal(a1, motivo="Venta")
        
        res = validar_crotal("0099")
        self.assertTrue(res['valido'])
        self.assertFalse(res['bloqueante'])
        self.assertIsNone(res['alerta'])

    def test_duplicate_crotal_detail_view_prioritizes_vivo(self):
        """
        Verificar que al haber un animal en BAJA y otro en VIVO con el mismo crotal,
        la vista de detalle /animales/<crotal>/ devuelve el VIVO sin MultipleObjectsReturned.
        """
        from django.urls import reverse

        # Animal histórico en BAJA
        a_baja = Animal.objects.create(
            crotal="6668",
            sexo="H",
            fecha_nacimiento=date(2018, 1, 1),
            estado_vital="BAJA",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )
        # Reutilización legítima de crotal: nuevo animal VIVO
        a_vivo = Animal.objects.create(
            crotal="6668",
            sexo="M",
            fecha_nacimiento=date(2023, 5, 10),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

        url = reverse('animal_detail', kwargs={'crotal': '6668'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['animal'].pk, a_vivo.pk)

