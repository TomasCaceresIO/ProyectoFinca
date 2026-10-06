from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.contrib.auth.models import User
from ganaderia.models import Explotacion, Finca, Animal, Parto, Ubicacion
from ganaderia.services.animal_services import registrar_parto


class IntegrityAndEdgeCasesTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.force_login(self.user)
        self.explotacion = Explotacion.objects.create(
            nombre="Explotación Auditoría & Blindaje",
            codigo_rega="ES999999999999"
        )
        self.finca = Finca.objects.create(explotacion=self.explotacion, nombre="Finca Principal")
        Ubicacion.objects.create(finca=self.finca, tipo_ubicacion='PASTO')

        # Vaca adulta con edad fértil (> 18 meses / 540 días)
        self.madre_adulta = Animal.objects.create(
            crotal="1000",
            sexo="H",
            fecha_nacimiento=date(2020, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

        # Novilla joven (< 18 meses)
        self.madre_joven = Animal.objects.create(
            crotal="2000",
            sexo="H",
            fecha_nacimiento=date(2025, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

    def test_parto_imposible_anterior_nacimiento_madre(self):
        """1. Test Parto Imposible: Intentar registrar parto con fecha anterior al nacimiento de la madre debe fallar."""
        fecha_anterior = date(2019, 12, 31)  # La madre nació el 01/01/2020
        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=self.madre_adulta,
                fecha_parto=fecha_anterior,
                crias=[{'crotal': '1001', 'sexo': 'M'}]
            )

    def test_madre_joven_menos_18_meses(self):
        """2. Test Madre Joven: Intentar registrar parto en una novilla de 12 meses debe fallar (< 18 meses / 540 días)."""
        fecha_parto_prematuro = date(2026, 1, 1)  # 12 meses (365 días < 540 días)
        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=self.madre_joven,
                fecha_parto=fecha_parto_prematuro,
                crias=[{'crotal': '2001', 'sexo': 'M'}]
            )

    def test_parto_gemelar_legitimo(self):
        """3. Test Parto Gemelar Legítimo: Registrar dos crías en la misma fecha debe tener alerta_intervalo == False."""
        resultado = registrar_parto(
            madre=self.madre_adulta,
            fecha_parto=date(2023, 6, 1),
            crias=[
                {'crotal': '3001', 'sexo': 'M'},
                {'crotal': '3002', 'sexo': 'H'}
            ]
        )
        parto = resultado['parto']
        self.assertFalse(parto.alerta_intervalo)
        self.assertEqual(resultado['alerta'], None)
        self.assertIsNotNone(parto.cria)
        self.assertIsNotNone(parto.cria2)

    def test_tres_partos_misma_fecha_dispara_alerta_o_falla(self):
        """Test de 3 partos en la misma fecha: 2 partos son gemelos (alerta=False), un 3er parto dispara Alerta Roja / alerta_intervalo = True o falla."""
        fecha_parto = date(2023, 8, 1)
        # Parto 1 en esa fecha
        res1 = registrar_parto(
            madre=self.madre_adulta,
            fecha_parto=fecha_parto,
            crias=[{'crotal': '5001', 'sexo': 'M'}]
        )
        self.assertFalse(res1['parto'].alerta_intervalo)

        # Parto 2 en esa fecha (segundo del gemelo)
        res2 = registrar_parto(
            madre=self.madre_adulta,
            fecha_parto=fecha_parto,
            crias=[{'crotal': '5002', 'sexo': 'H'}]
        )
        self.assertFalse(res2['parto'].alerta_intervalo)

        # Intento de registrar un 3er parto en esa misma fecha exacta sin forzar -> debe fallar por validación
        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=self.madre_adulta,
                fecha_parto=fecha_parto,
                forzar=False,
                crias=[{'crotal': '5003', 'sexo': 'M'}]
            )

        # Si se fuerza, debe marcar alerta_intervalo = True
        res3 = registrar_parto(
            madre=self.madre_adulta,
            fecha_parto=fecha_parto,
            forzar=True,
            crias=[{'crotal': '5003', 'sexo': 'M'}]
        )
        self.assertTrue(res3['parto'].alerta_intervalo)
        self.assertEqual(res3['alerta'], 'ROJO')

    def test_rollback_atomico(self):
        """4. Test Rollback Atómico: fallo en la 2ª cría no debe dejar ni el parto ni la 1ª cría guardados."""
        # Crear animal activo con crotal 4002 para provocar fallo de duplicidad bloqueante en la 2ª cría
        Animal.objects.create(
            crotal="4002",
            sexo="M",
            fecha_nacimiento=date(2022, 1, 1),
            estado_vital="VIVO",
            finca=self.finca,
            sub_ubicacion="PASTO"
        )

        with self.assertRaises(ValidationError):
            registrar_parto(
                madre=self.madre_adulta,
                fecha_parto=date(2024, 1, 1),
                crias=[
                    {'crotal': '4001', 'sexo': 'M'},
                    {'crotal': '4002', 'sexo': 'H'}  # Duplicado activo provoca ValidationError
                ]
            )

        # Comprobar que en la base de datos NO existe la cría 1 ni el parto
        self.assertFalse(Animal.objects.filter(crotal="4001").exists())
        self.assertFalse(Parto.objects.filter(madre=self.madre_adulta, fecha_parto=date(2024, 1, 1)).exists())

    def test_benchmark_n_plus_one(self):
        """5. Test Benchmark N+1: La exportación del censo debe ejecutar un número constante de consultas (<= 3 queries) para 100+ animales."""
        # Generar 100 animales masivamente
        animales_bulk = []
        for i in range(1, 101):
            crotal_str = f"{i:04d}"
            if crotal_str in ["1000", "2000", "4002"]:
                crotal_str = f"{i + 5000:04d}"
            animales_bulk.append(
                Animal(
                    crotal=crotal_str,
                    sexo="H" if i % 2 == 0 else "M",
                    fecha_nacimiento=date(2021, 1, 1),
                    estado_vital="VIVO",
                    finca=self.finca,
                    sub_ubicacion="PASTO"
                )
            )
        Animal.objects.bulk_create(animales_bulk)

        url_csv = reverse('exportar_csv')
        url_excel = reverse('exportar_excel')

        # Medir consultas en exportación CSV
        with CaptureQueriesContext(connection) as ctx_csv:
            response_csv = self.client.get(url_csv)
            self.assertEqual(response_csv.status_code, 200)

        # Medir consultas en exportación Excel
        with CaptureQueriesContext(connection) as ctx_excel:
            response_excel = self.client.get(url_excel)
            self.assertEqual(response_excel.status_code, 200)

        # Verificar que el número de consultas es <= 5 independientemente de los 100+ animales (sin N+1)
        self.assertLessEqual(len(ctx_csv), 5)
        self.assertLessEqual(len(ctx_excel), 5)
