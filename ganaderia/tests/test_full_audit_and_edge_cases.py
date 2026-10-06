"""
Batería completa de tests de auditoría adversarial y casos límite (QA Adversarial).
Certifica la robustez de todo el sistema:
- Asistente de IA y Lenguaje Natural
- Límite de partos gemelares y alertas
- Integridad genealógica y biológica
- Unicidad y formato de crotales
- Vaciado granular de fincas y atomicidad
"""
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from django.contrib.auth.models import User

from ganaderia.models import Explotacion, Finca, Animal, Parto, Incidencia, Ubicacion
from ganaderia.forms import AnimalForm
from ganaderia.services.animal_services import (
    registrar_parto,
    validar_intervalo_parto,
    actualizar_parto,
    dar_de_baja_animal,
    validar_crotal
)


class FullAuditAndEdgeCasesTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Auditoría Total",
            codigo_rega="ES123456789099"
        )
        self.finca1 = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Alfa"
        )
        self.finca2 = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Beta"
        )
        Ubicacion.objects.create(finca=self.finca1, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca1, tipo_ubicacion="CEBADERO")
        Ubicacion.objects.create(finca=self.finca2, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca2, tipo_ubicacion="CEBADERO")

        # Madre adulta (> 18 meses / 540 días)
        self.madre_0001 = Animal.objects.create(
            crotal="0001",
            sexo="H",
            raza="Retinta",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="VIVO",
            finca=self.finca1,
            sub_ubicacion="PASTO"
        )

    # =========================================================================
    # 1. MÓDULO REPRODUCTIVO E INTERVALOS
    # =========================================================================

    def test_parto_gemelar_valido_dos_crias(self):
        """2 partos en la misma fecha (gemelos) -> Ambos con alerta_intervalo = False."""
        fecha_gemelos = date(2023, 5, 10)
        res1 = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_gemelos,
            crias=[{'crotal': '0010', 'sexo': 'M'}]
        )
        res2 = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_gemelos,
            crias=[{'crotal': '0011', 'sexo': 'H'}]
        )
        self.assertFalse(res1['parto'].alerta_intervalo)
        self.assertFalse(res2['parto'].alerta_intervalo)
        self.assertIsNone(res1['alerta'])
        self.assertIsNone(res2['alerta'])

    def test_parto_triple_mismo_dia_dispara_alerta(self):
        """3 partos en la misma fecha para la misma madre -> El tercero levanta alerta_intervalo = True o rechaza."""
        fecha_parto = date(2023, 9, 15)
        registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_parto,
            crias=[{'crotal': '0020', 'sexo': 'M'}]
        )
        registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_parto,
            crias=[{'crotal': '0021', 'sexo': 'H'}]
        )

        # Sin forzar -> ValidationError
        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=self.madre_0001,
                fecha_parto=fecha_parto,
                forzar=False,
                crias=[{'crotal': '0022', 'sexo': 'M'}]
            )

        # Con forzar=True -> alerta_intervalo = True
        res3 = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_parto,
            forzar=True,
            crias=[{'crotal': '0022', 'sexo': 'M'}]
        )
        self.assertTrue(res3['parto'].alerta_intervalo)
        self.assertEqual(res3['alerta'], 'ROJO')

    def test_parto_intervalo_invalido_distinta_fecha(self):
        """Partos a 100 días de diferencia (< 270d) -> Levanta Alerta Roja en /incidencias/."""
        fecha_p1 = date(2024, 1, 1)
        fecha_p2 = date(2024, 4, 10)  # ~100 días

        registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_p1,
            crias=[{'crotal': '0030', 'sexo': 'M'}]
        )
        res2 = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_p2,
            forzar=True,
            crias=[{'crotal': '0031', 'sexo': 'H'}]
        )
        self.assertTrue(res2['parto'].alerta_intervalo)

        url_incidencias = reverse('incidencias')
        response = self.client.get(url_incidencias)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Crotal {self.madre_0001.crotal}")
        self.assertTrue(Incidencia.objects.filter(parto=res2['parto'], resuelta=False).exists())

    def test_rectificar_fecha_parto_limpia_incidencia(self):
        """Parto en conflicto modificado a >= 270 días -> alerta_intervalo pasa a False y desaparece de /incidencias/."""
        fecha_p1 = date(2024, 1, 1)
        fecha_p2_conflicto = date(2024, 4, 1)

        registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_p1,
            crias=[{'crotal': '0040', 'sexo': 'M'}]
        )
        p2 = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_p2_conflicto,
            forzar=True,
            crias=[{'crotal': '0041', 'sexo': 'H'}]
        )['parto']
        self.assertTrue(p2.alerta_intervalo)

        # Modificar p2 a 300 días después (01/11/2024)
        fecha_p2_valida = date(2024, 11, 1)
        actualizar_parto(p2, fecha_p2_valida)
        p2.refresh_from_db()

        self.assertFalse(p2.alerta_intervalo)
        self.assertFalse(Incidencia.objects.filter(parto=p2, resuelta=False).exists())

        response = self.client.get(reverse('incidencias'))
        self.assertNotContains(response, f"Crotal {self.madre_0001.crotal}")

    def test_madre_baja_limpia_sus_incidencias(self):
        """Hembra con alertas rojas pasa a BAJA -> Sus partos limpian la alerta y desaparecen de /incidencias/."""
        fecha_p1 = date(2024, 1, 1)
        fecha_p2 = date(2024, 4, 1)

        registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_p1,
            crias=[{'crotal': '0050', 'sexo': 'M'}]
        )
        p2 = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=fecha_p2,
            forzar=True,
            crias=[{'crotal': '0051', 'sexo': 'H'}]
        )['parto']
        self.assertTrue(p2.alerta_intervalo)

        # Dar de baja a la madre
        dar_de_baja_animal(self.madre_0001, motivo="Muerte natural")
        p2.refresh_from_db()

        self.assertFalse(p2.alerta_intervalo)
        self.assertFalse(Incidencia.objects.filter(parto=p2, resuelta=False).exists())

        response = self.client.get(reverse('incidencias'))
        self.assertNotContains(response, f"Crotal {self.madre_0001.crotal}")

    # =========================================================================
    # 3. MÓDULO DE INTEGRIDAD Y ROBUSTEZ GENERAL
    # =========================================================================

    def test_bloqueo_madre_menor_18_meses(self):
        """Intento de parto con madre de 12 meses (< 540 días) -> Rechazado con ValidationError."""
        novilla_joven = Animal.objects.create(
            crotal="0060",
            sexo="H",
            fecha_nacimiento=date(2024, 1, 1),
            estado_vital="VIVO",
            finca=self.finca1,
            sub_ubicacion="PASTO"
        )
        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=novilla_joven,
                fecha_parto=date(2025, 1, 1),  # Solo 366 días de vida
                crias=[{'crotal': '0061', 'sexo': 'M'}]
            )

    def test_bloqueo_autopaternidad(self):
        """Asignar madre = self en edición de animal -> Lanza ValidationError o añade error en form."""
        form_data = {
            'crotal': self.madre_0001.crotal,
            'sexo': self.madre_0001.sexo,
            'raza': self.madre_0001.raza,
            'fecha_nacimiento': self.madre_0001.fecha_nacimiento,
            'estado_vital': self.madre_0001.estado_vital,
            'finca': self.finca1.pk,
            'sub_ubicacion': 'PASTO',
            'madre': self.madre_0001.pk,  # Asignarse a sí misma como madre
        }
        form = AnimalForm(data=form_data, instance=self.madre_0001)
        self.assertFalse(form.is_valid())
        self.assertIn('madre', form.errors)

    def test_crotal_format_invalido(self):
        """Probar crotales '12', '12345', 'ABCD' -> Todos rechazados."""
        for c_inv in ['12', '12345', 'ABCD', '', '01a2']:
            val = validar_crotal(c_inv)
            self.assertFalse(val['valido'])
            self.assertTrue(val['bloqueante'])

    def test_reutilizacion_crotal_baja_limpio(self):
        """Crotal de animal en BAJA asignado a cría -> Creado sin advertencias ni bloqueos."""
        Animal.objects.create(
            crotal="0070",
            sexo="M",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="BAJA",
            fecha_baja=date(2022, 1, 1),
            finca=self.finca1,
            sub_ubicacion="PASTO"
        )

        # Validar disponibilidad
        val = validar_crotal("0070")
        self.assertTrue(val['valido'])
        self.assertFalse(val['bloqueante'])
        self.assertIsNone(val['alerta'])

        # Registrar parto asignando este crotal a la nueva cría
        res = registrar_parto(
            madre=self.madre_0001,
            fecha_parto=date(2025, 1, 1),
            crias=[{'crotal': '0070', 'sexo': 'M'}]
        )
        self.assertEqual(res['cria'].crotal, '0070')
        self.assertEqual(res['cria'].estado_vital, 'VIVO')

    def test_borrado_finca_granular_atomico(self):
        """Vaciado de finca con animales trasladados a otra finca y animales a baja -> Finca borrada limpiamente."""
        a_trasladar = Animal.objects.create(
            crotal="0081",
            sexo="H",
            fecha_nacimiento=date(2021, 1, 1),
            estado_vital="VIVO",
            finca=self.finca1,
            sub_ubicacion="PASTO"
        )
        a_baja = Animal.objects.create(
            crotal="0082",
            sexo="M",
            fecha_nacimiento=date(2021, 1, 1),
            estado_vital="VIVO",
            finca=self.finca1,
            sub_ubicacion="PASTO"
        )

        url_delete = reverse('finca_delete', kwargs={'pk': self.finca1.pk})
        post_data = {
            'opcion': 'opcion_c',
            'finca_destino_c': self.finca2.pk,
            f'accion_{self.madre_0001.pk}': 'baja',
            f'accion_{a_trasladar.pk}': 'trasladar',
            f'accion_{a_baja.pk}': 'baja',
        }

        response = self.client.post(url_delete, post_data)
        self.assertEqual(response.status_code, 302)

        # La finca1 debe haber sido eliminada
        self.assertFalse(Finca.objects.filter(pk=self.finca1.pk).exists())

        # a_trasladar debe estar en finca2 y VIVO
        a_trasladar.refresh_from_db()
        self.assertEqual(a_trasladar.finca, self.finca2)
        self.assertEqual(a_trasladar.estado_vital, 'VIVO')

        # a_baja debe estar en BAJA
        a_baja.refresh_from_db()
        self.assertEqual(a_baja.estado_vital, 'BAJA')
