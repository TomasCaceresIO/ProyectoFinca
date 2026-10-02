"""
Comando de gestión para cargar datos de prueba iniciales (MVP Ganadero).
"""
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from ganaderia.models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia


class Command(BaseCommand):
    help = 'Carga 1 Explotación, 2 Fincas, recintos y 15 animales de prueba.'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Borrando datos anteriores...")
        Incidencia.objects.all().delete()
        Parto.objects.all().delete()
        Animal.objects.all().delete()
        Ubicacion.objects.all().delete()
        Finca.objects.all().delete()
        Explotacion.objects.all().delete()

        self.stdout.write("Creando Explotacion y Fincas...")
        exp = Explotacion.objects.create(
            nombre="Explotación Ganadera La Dehesa",
            codigo_rega="ES-190420001234"
        )

        finca1 = Finca.objects.create(explotacion=exp, nombre="Finca La Esperanza")
        finca2 = Finca.objects.create(explotacion=exp, nombre="Finca El Valle")

        for f in [finca1, finca2]:
            Ubicacion.objects.create(finca=f, tipo_ubicacion='PASTO')
            Ubicacion.objects.create(finca=f, tipo_ubicacion='CEBADERO')
            Ubicacion.objects.create(finca=f, tipo_ubicacion='APARTADO')

        hoy = date.today()

        self.stdout.write("Creando 15 animales de prueba...")
        
        # 1. Animales Hembras en Pasto (Madres)
        m1 = Animal.objects.create(
            crotal="0001", sexo="H", fecha_nacimiento=hoy - timedelta(days=1500),
            raza="RETINTA", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO"
        )
        m2 = Animal.objects.create(
            crotal="0002", sexo="H", fecha_nacimiento=hoy - timedelta(days=1800),
            raza="CHAROLESA", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO"
        )
        m3 = Animal.objects.create(
            crotal="0003", sexo="H", fecha_nacimiento=hoy - timedelta(days=1200),
            raza="LIMUSINA", estado_vital="VIVO", finca=finca2, sub_ubicacion="PASTO"
        )

        # 2. Animales en Cebadero (con fecha_entrada_cebadero)
        c1 = Animal.objects.create(
            crotal="0004", sexo="M", fecha_nacimiento=hoy - timedelta(days=400),
            raza="CRUZADO", estado_vital="VIVO", finca=finca1, sub_ubicacion="CEBADERO",
            fecha_entrada_cebadero=hoy - timedelta(days=60)
        )
        c2 = Animal.objects.create(
            crotal="0005", sexo="M", fecha_nacimiento=hoy - timedelta(days=450),
            raza="CRUZADO", estado_vital="VIVO", finca=finca2, sub_ubicacion="CEBADERO",
            fecha_entrada_cebadero=hoy - timedelta(days=45)
        )

        # 3. Animales en Apartado
        a1 = Animal.objects.create(
            crotal="0006", sexo="H", fecha_nacimiento=hoy - timedelta(days=900),
            raza="SIMMENTAL", estado_vital="VIVO", finca=finca1, sub_ubicacion="APARTADO"
        )

        # 4. Crías de las madres m1, m2
        cria1 = Animal.objects.create(
            crotal="0007", sexo="M", fecha_nacimiento=hoy - timedelta(days=300),
            raza="RETINTA", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO", madre=m1
        )
        cria2 = Animal.objects.create(
            crotal="0008", sexo="H", fecha_nacimiento=hoy - timedelta(days=80),
            raza="RETINTA", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO", madre=m1
        )
        cria3 = Animal.objects.create(
            crotal="0009", sexo="H", fecha_nacimiento=hoy - timedelta(days=350),
            raza="CHAROLESA", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO", madre=m2
        )

        # 5. Más animales para completar censo
        Animal.objects.create(crotal="0010", sexo="M", fecha_nacimiento=hoy - timedelta(days=700), raza="ANGUS", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO")
        Animal.objects.create(crotal="0011", sexo="H", fecha_nacimiento=hoy - timedelta(days=600), raza="HEREFORD", estado_vital="VIVO", finca=finca2, sub_ubicacion="PASTO")
        Animal.objects.create(crotal="0012", sexo="M", fecha_nacimiento=hoy - timedelta(days=500), raza="CRUZADO", estado_vital="VIVO", finca=finca2, sub_ubicacion="PASTO")
        Animal.objects.create(crotal="0013", sexo="H", fecha_nacimiento=hoy - timedelta(days=1100), raza="FRISONA", estado_vital="VIVO", finca=finca1, sub_ubicacion="PASTO")

        # 6. Animal en BAJA (para probar crotal histórico "0099")
        baja = Animal.objects.create(
            crotal="0099", sexo="M", fecha_nacimiento=hoy - timedelta(days=1000),
            raza="CRUZADO", estado_vital="BAJA", fecha_baja=hoy - timedelta(days=100),
            motivo_baja="Venta para sacrificio", finca=finca1, sub_ubicacion="PASTO"
        )
        
        # 7. Animal sin partos adicionales
        Animal.objects.create(crotal="0014", sexo="H", fecha_nacimiento=hoy - timedelta(days=800), raza="RETINTA", estado_vital="VIVO", finca=finca2, sub_ubicacion="PASTO")

        self.stdout.write("Registrando partos...")
        # Parto normal para m2
        Parto.objects.create(
            madre=m2, fecha_parto=hoy - timedelta(days=350), cria=cria3, alerta_intervalo=False
        )

        # Partos para m1 con ALERTA ROJA (< 270 días)
        # Parto 1: hace 300 días
        p1 = Parto.objects.create(
            madre=m1, fecha_parto=hoy - timedelta(days=300), cria=cria1, alerta_intervalo=False
        )
        # Parto 2: hace 80 días (intervalo 220 días < 270d) -> ALERTA ROJA
        p2 = Parto.objects.create(
            madre=m1, fecha_parto=hoy - timedelta(days=80), cria=cria2, alerta_intervalo=True,
            observaciones="Parto prematuro / intervalo ajustado"
        )

        Incidencia.objects.create(
            animal=m1,
            tipo="ROJO",
            descripcion="Intervalo entre partos de 220 dias (inferior a 270d).",
            parto=p2
        )

        self.stdout.write(self.style.SUCCESS("Datos de prueba cargados correctamente."))
