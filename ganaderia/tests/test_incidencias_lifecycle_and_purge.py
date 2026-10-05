from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from ganaderia.models import Explotacion, Finca, Animal, Parto, Incidencia, Ubicacion
from ganaderia.services.animal_services import registrar_parto, dar_de_baja_animal


class IncidenciasLifecycleAndPurgeTestCase(TestCase):
    """
    Batería de pruebas automatizadas para el ciclo de vida de incidencias,
    filtrado riguroso del contador de partos activos y purgado masivo de historiales.
    """
    def setUp(self):
        self.client = Client()
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Incidencias QA",
            codigo_rega="ES999999999999"
        )
        self.finca = Finca.objects.create(
            explotacion=self.explotacion,
            nombre="Finca Pruebas Incidencias"
        )
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="PASTO")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion="CEBADERO")

        self.madre = Animal.objects.create(
            crotal="1111",
            sexo="H",
            raza="Retinta",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

    def test_exclusion_contador_al_dar_de_baja_madre(self):
        """
        1. Test de exclusión en contador:
        Crear madre con parto en conflicto (< 270d) -> Se genera Alerta Roja.
        Dar de baja a la madre -> El contador de incidencias activas pasa a 0 y desaparece de la vista.
        """
        # Parto 1
        registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 1, 1),
            crias=[{'crotal': '5001', 'sexo': 'H', 'raza': 'Retinta'}]
        )
        # Parto 2 en conflicto (60 días de diferencia, < 270 días)
        p2_res = registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 3, 2),
            crias=[{'crotal': '5002', 'sexo': 'M', 'raza': 'Retinta'}],
            forzar=True
        )
        p2 = p2_res['parto']
        self.assertTrue(p2.alerta_intervalo)

        # Contador global activo debe ser 1
        contador_antes = Parto.objects.filter(
            alerta_intervalo=True,
            madre__estado_vital='VIVO'
        ).count()
        self.assertEqual(contador_antes, 1)

        # Verificar en el context processor de /incidencias/
        res = self.client.get(reverse('incidencias'))
        self.assertEqual(res.context['incidencias_count'], 1)

        # Dar de baja a la madre
        dar_de_baja_animal(self.madre, motivo="Venta o sacrificio")

        # Comprobar que el contador para animales activos pasa exactamente a 0
        contador_despues = Parto.objects.filter(
            alerta_intervalo=True,
            madre__estado_vital='VIVO'
        ).count()
        self.assertEqual(contador_despues, 0)

        # Comprobar en vista /incidencias/
        res_despues = self.client.get(reverse('incidencias'))
        self.assertEqual(res_despues.context['incidencias_count'], 0)
        self.assertEqual(len(res_despues.context['incidencias_activas']), 0)

    def test_purgado_masivo_de_bajas(self):
        """
        2. Test de purgado masivo de bajas:
        Crear 3 animales en estado BAJA y 2 animales en estado VIVO.
        Ejecutar POST /incidencias/bajas/purgar/ -> Los 3 en baja se eliminan y los 2 vivos quedan intactos.
        """
        # Crear 3 bajas
        baja1 = Animal.objects.create(crotal="9001", sexo="H", fecha_nacimiento=date(2021, 1, 1), estado_vital="BAJA", finca=self.finca)
        baja2 = Animal.objects.create(crotal="9002", sexo="M", fecha_nacimiento=date(2021, 2, 1), estado_vital="BAJA", finca=self.finca)
        baja3 = Animal.objects.create(crotal="9003", sexo="H", fecha_nacimiento=date(2021, 3, 1), estado_vital="BAJA", finca=self.finca)

        # Crear 2 vivos adicionales (además de self.madre)
        vivo1 = Animal.objects.create(crotal="8001", sexo="H", fecha_nacimiento=date(2022, 1, 1), estado_vital="VIVO", finca=self.finca)
        vivo2 = Animal.objects.create(crotal="8002", sexo="M", fecha_nacimiento=date(2022, 2, 1), estado_vital="VIVO", finca=self.finca)

        total_bajas_antes = Animal.objects.filter(estado_vital='BAJA').count()
        total_vivos_antes = Animal.objects.filter(estado_vital='VIVO').count()
        self.assertEqual(total_bajas_antes, 3)
        self.assertEqual(total_vivos_antes, 3)  # self.madre + 2 vivos

        # Ejecutar purgado masivo
        url_purga = reverse('incidencias_bajas_purgar')
        response = self.client.post(url_purga)
        self.assertRedirects(response, reverse('incidencias') + '?tab=bajas')

        # Comprobar base de datos
        self.assertEqual(Animal.objects.filter(estado_vital='BAJA').count(), 0)
        self.assertEqual(Animal.objects.filter(estado_vital='VIVO').count(), total_vivos_antes)
        self.assertTrue(Animal.objects.filter(crotal="8001", estado_vital="VIVO").exists())
        self.assertTrue(Animal.objects.filter(crotal="8002", estado_vital="VIVO").exists())
        self.assertTrue(Animal.objects.filter(crotal=self.madre.crotal, estado_vital="VIVO").exists())

    def test_rectificacion_alerta_parto(self):
        """
        3. Test de rectificación de alerta:
        Rectificar fecha a >= 270 días -> alerta_intervalo pasa a False y desaparece del query del contador.
        """
        registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 1, 1),
            crias=[{'crotal': '6001', 'sexo': 'H', 'raza': 'Retinta'}]
        )
        res_parto2 = registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 2, 1),  # Solo 31 días de intervalo (< 270d)
            crias=[{'crotal': '6002', 'sexo': 'M', 'raza': 'Retinta'}],
            forzar=True
        )
        parto2 = res_parto2['parto']
        self.assertTrue(parto2.alerta_intervalo)

        incidencia = Incidencia.objects.filter(parto=parto2, resuelta=False).first()
        self.assertIsNotNone(incidencia)

        # Rectificar vía endpoint /incidencias/<pk>/rectificar/ asignando fecha con > 270 días (ej. 2023-11-01)
        url_rectificar = reverse('incidencia_rectificar', kwargs={'pk': incidencia.pk})
        post_data = {
            'nueva_fecha_parto': '2023-11-01',
            'observaciones': 'Fecha rectificada por error administrativo'
        }
        res_post = self.client.post(url_rectificar, post_data)
        self.assertEqual(res_post.status_code, 302)

        parto2.refresh_from_db()
        self.assertFalse(parto2.alerta_intervalo)
        self.assertEqual(parto2.fecha_parto, date(2023, 11, 1))

        # Incidencia debe estar resuelta
        incidencia.refresh_from_db()
        self.assertTrue(incidencia.resuelta)

        # El contador debe ser 0
        contador = Parto.objects.filter(
            alerta_intervalo=True,
            madre__estado_vital='VIVO'
        ).count()
        self.assertEqual(contador, 0)

    def test_rectificacion_htmx_actualiza_counter(self):
        """
        Verifica que al rectificar mediante petición HTMX se devuelve la respuesta OOB para #incidencias-counter.
        """
        registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 1, 1),
            crias=[{'crotal': '7001', 'sexo': 'H', 'raza': 'Retinta'}]
        )
        res_parto2 = registrar_parto(
            madre=self.madre,
            fecha_parto=date(2023, 2, 1),
            crias=[{'crotal': '7002', 'sexo': 'M', 'raza': 'Retinta'}],
            forzar=True
        )
        parto2 = res_parto2['parto']
        inc = Incidencia.objects.filter(parto=parto2).first()

        url_rectificar = reverse('incidencia_rectificar', kwargs={'pk': inc.pk})
        res_htmx = self.client.post(
            url_rectificar,
            {'nueva_fecha_parto': '2023-11-01'},
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(res_htmx.status_code, 200)
        self.assertContains(res_htmx, 'id="incidencias-counter"')
        self.assertContains(res_htmx, 'hx-swap-oob="true"')

    def test_limpiar_alertas_huerfanas_y_no_activas(self):
        """
        Verifica que POST /incidencias/alertas/limpiar/ sanea cualquier alerta_intervalo en vacas en baja.
        """
        # Forzar un parto con alerta en una vaca en baja
        self.madre.estado_vital = 'BAJA'
        self.madre.save()
        p = Parto.objects.create(madre=self.madre, fecha_parto=date(2023, 1, 1), alerta_intervalo=True)

        url_limpiar = reverse('incidencias_alertas_limpiar')
        res = self.client.post(url_limpiar)
        self.assertRedirects(res, reverse('incidencias'))

        p.refresh_from_db()
        self.assertFalse(p.alerta_intervalo)
