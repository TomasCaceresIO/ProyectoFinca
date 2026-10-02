"""
Comando de gestion para cargar datos de demostracion.
Uso: python manage.py seed_demo_data

Carga:
- 1 Explotacion (Ganaderia Las Encinas)
- 2 Fincas con sus ubicaciones (PASTO, CEBADERO, APARTADO)
- 15 animales de prueba incluyendo:
  * Reproductoras en pasto
  * Machos en cebadero
  * Un parto con intervalo < 270 dias (alerta roja RN-04)
  * Un crotal historico en baja para probar alerta amarilla (RN-03)
"""
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from ganaderia.models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia


class Command(BaseCommand):
    help = 'Carga datos de demostracion para el proyecto Gestion Ganadera'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Eliminar todos los datos existentes antes de cargar',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['reset']:
            self.stdout.write('[*] Eliminando datos existentes...')
            Incidencia.objects.all().delete()
            Parto.objects.all().delete()
            Animal.objects.all().delete()
            Ubicacion.objects.all().delete()
            Finca.objects.all().delete()
            Explotacion.objects.all().delete()
            self.stdout.write(self.style.WARNING('Datos eliminados.'))

        self.stdout.write('[*] Cargando datos de demostracion...')

        # --- 1. EXPLOTACION ---
        explotacion, created = Explotacion.objects.get_or_create(
            codigo_rega='ES-190420001234',
            defaults={'nombre': 'Ganaderia Las Encinas'}
        )
        if created:
            self.stdout.write(f'  [OK] Explotacion: {explotacion}')
        else:
            self.stdout.write(f'  [--] Explotacion ya existe: {explotacion}')

        # --- 2. FINCAS ---
        finca_encinas, _ = Finca.objects.get_or_create(
            nombre='Finca Las Encinas',
            explotacion=explotacion
        )
        finca_robledal, _ = Finca.objects.get_or_create(
            nombre='Finca El Robledal',
            explotacion=explotacion
        )
        self.stdout.write(f'  [OK] Fincas: {finca_encinas.nombre}, {finca_robledal.nombre}')

        # --- 3. UBICACIONES ---
        # Finca Encinas: PASTO, CEBADERO, APARTADO
        ub_pasto_encinas, _ = Ubicacion.objects.get_or_create(
            finca=finca_encinas, tipo='PASTO'
        )
        ub_cebadero_encinas, _ = Ubicacion.objects.get_or_create(
            finca=finca_encinas, tipo='CEBADERO'
        )
        ub_apartado_encinas, _ = Ubicacion.objects.get_or_create(
            finca=finca_encinas, tipo='APARTADO'
        )
        # Finca Robledal: PASTO, APARTADO
        ub_pasto_robledal, _ = Ubicacion.objects.get_or_create(
            finca=finca_robledal, tipo='PASTO'
        )
        ub_apartado_robledal, _ = Ubicacion.objects.get_or_create(
            finca=finca_robledal, tipo='APARTADO'
        )
        self.stdout.write('  [OK] Ubicaciones creadas (PASTO, CEBADERO, APARTADO)')

        # --- 4. ANIMALES ---
        hoy = date.today()
        animales_data = [
            # (crotal, sexo, raza, fecha_nac, finca, ubicacion, estado, dias_baja)
            # Reproductoras en pasto Encinas
            ('1001', 'H', 'RETINTA',   hoy - timedelta(days=5*365), finca_encinas, ub_pasto_encinas,    'VIVO', None),
            ('1002', 'H', 'RETINTA',   hoy - timedelta(days=6*365), finca_encinas, ub_pasto_encinas,    'VIVO', None),
            ('1003', 'H', 'LIMUSINA',  hoy - timedelta(days=4*365), finca_encinas, ub_pasto_encinas,    'VIVO', None),
            ('1004', 'H', 'CHAROLESA', hoy - timedelta(days=7*365), finca_encinas, ub_pasto_encinas,    'VIVO', None),
            ('1005', 'H', 'RETINTA',   hoy - timedelta(days=3*365), finca_encinas, ub_pasto_encinas,    'VIVO', None),
            # Machos en cebadero Encinas
            ('2001', 'M', 'CRUZADO',   hoy - timedelta(days=365),   finca_encinas, ub_cebadero_encinas, 'VIVO', None),
            ('2002', 'M', 'CHAROLESA', hoy - timedelta(days=400),   finca_encinas, ub_cebadero_encinas, 'VIVO', None),
            ('2003', 'M', 'RETINTA',   hoy - timedelta(days=500),   finca_encinas, ub_cebadero_encinas, 'VIVO', None),
            # Animales en Robledal
            ('3001', 'H', 'ANGUS',     hoy - timedelta(days=4*365), finca_robledal, ub_pasto_robledal,  'VIVO', None),
            ('3002', 'H', 'SIMMENTAL', hoy - timedelta(days=5*365), finca_robledal, ub_pasto_robledal,  'VIVO', None),
            ('3003', 'M', 'CRUZADO',   hoy - timedelta(days=300),   finca_robledal, ub_pasto_robledal,  'VIVO', None),
            # Terneros recientes en apartado
            ('4001', 'H', 'RETINTA',   hoy - timedelta(days=60),    finca_encinas, ub_apartado_encinas, 'VIVO', None),
            ('4002', 'M', 'CRUZADO',   hoy - timedelta(days=90),    finca_encinas, ub_apartado_encinas, 'VIVO', None),
            # Crotal historico en BAJA (para probar RN-03 - Alerta Amarilla)
            ('9999', 'M', 'CRUZADO',   hoy - timedelta(days=2*365), finca_encinas, ub_pasto_encinas,    'BAJA', 180),
            # Animal en apartado Robledal
            ('5001', 'H', 'HEREFORD',  hoy - timedelta(days=3*365), finca_robledal, ub_apartado_robledal, 'VIVO', None),
        ]

        animales_creados = {}
        for crotal, sexo, raza, fecha_nac, finca, ubicacion, estado, dias_baja in animales_data:
            animal, created = Animal.objects.get_or_create(
                crotal=crotal,
                estado_vital=estado,
                defaults={
                    'sexo': sexo,
                    'raza': raza,
                    'fecha_nacimiento': fecha_nac,
                    'finca_actual': finca,
                    'sub_ubicacion': ubicacion,
                    'estado_vital': estado,
                    'fecha_baja': hoy - timedelta(days=dias_baja) if dias_baja else None,
                    'motivo_baja': 'Venta a mercado' if estado == 'BAJA' else '',
                }
            )
            if created and estado == 'VIVO' and ubicacion.tipo == 'CEBADERO':
                animal.fecha_entrada_cebadero = hoy - timedelta(days=60)
                animal.save()
            animales_creados[crotal] = animal
            status_icon = '[OK]' if created else '[--]'
            self.stdout.write(f'  {status_icon} Animal crotal {crotal} ({estado})')

        # --- 5. PARTOS ---
        self.stdout.write('\n  Creando historial de partos...')

        madre_1001 = animales_creados.get('1001')
        cria_4001 = animales_creados.get('4001')

        if madre_1001:
            # Parto 1: hace ~18 meses (normal, intervalo OK)
            parto1, _ = Parto.objects.get_or_create(
                madre=madre_1001,
                fecha_parto=hoy - timedelta(days=548),
                defaults={
                    'alerta_intervalo': False,
                    'observaciones': 'Parto normal. Ternero sano.',
                }
            )
            # Parto 2: hace 60 dias (con cria 4001, intervalo 488 dias - OK)
            parto2, _ = Parto.objects.get_or_create(
                madre=madre_1001,
                fecha_parto=hoy - timedelta(days=60),
                defaults={
                    'cria': cria_4001,
                    'alerta_intervalo': False,
                    'observaciones': 'Parto normal. Ternera registrada.',
                }
            )
            if cria_4001:
                cria_4001.madre = madre_1001
                cria_4001.save()
            self.stdout.write('  [OK] Partos de reproductora 1001 creados (intervalo correcto)')

        # Reproductora 1002: parto con intervalo CORTO (< 270 dias) -> Alerta Roja RN-04
        madre_1002 = animales_creados.get('1002')
        cria_4002 = animales_creados.get('4002')
        if madre_1002:
            # Parto anterior (hace 300 dias)
            parto_prev, _ = Parto.objects.get_or_create(
                madre=madre_1002,
                fecha_parto=hoy - timedelta(days=300),
                defaults={
                    'alerta_intervalo': False,
                    'observaciones': 'Parto normal anterior.',
                }
            )
            # Parto nuevo (hace 60 dias -> intervalo = 240 dias < 270 -> ALERTA ROJA)
            parto_conflicto, created_p = Parto.objects.get_or_create(
                madre=madre_1002,
                fecha_parto=hoy - timedelta(days=60),
                defaults={
                    'cria': cria_4002,
                    'alerta_intervalo': True,
                    'observaciones': 'PARTO CON INTERVALO CORTO - Generado para pruebas RN-04.',
                }
            )
            if created_p:
                Incidencia.objects.get_or_create(
                    animal=madre_1002,
                    parto=parto_conflicto,
                    defaults={
                        'tipo': 'ROJO',
                        'descripcion': (
                            'Intervalo entre partos de 240 dias '
                            '(minimo: 270 dias). '
                            f'Parto anterior: {parto_prev.fecha_parto}. '
                            '(Incidencia de DEMO - RN-04)'
                        ),
                        'resuelta': False,
                    }
                )
                self.stdout.write(
                    self.style.WARNING(
                        '  [ALERTA ROJA] RN-04: Crotal 1002 - Intervalo 240 dias entre partos'
                    )
                )

        # Incidencia de alerta amarilla (crotal 9999 en baja)
        animal_baja_9999 = animales_creados.get('9999')
        if animal_baja_9999:
            Incidencia.objects.get_or_create(
                animal=animal_baja_9999,
                tipo='AMARILLO',
                defaults={
                    'descripcion': (
                        'DEMO: El crotal 9999 esta dado de baja. '
                        'Al intentar registrar un nuevo animal con crotal 9999, '
                        'el sistema emitira Alerta Amarilla (RN-03).'
                    ),
                    'resuelta': False,
                }
            )
            self.stdout.write(
                self.style.WARNING(
                    '  [ALERTA AMARILLA] RN-03 de DEMO: Crotal 9999 en BAJA. '
                    'Prueba a crear un animal con ese crotal.'
                )
            )

        # --- RESUMEN FINAL ---
        self.stdout.write('\n' + '-' * 50)
        self.stdout.write(self.style.SUCCESS('[OK] Datos de demostracion cargados correctamente.'))
        self.stdout.write(f'   Explotacion: {explotacion.nombre} ({explotacion.codigo_rega})')
        self.stdout.write(f'   Fincas: {Finca.objects.count()}')
        self.stdout.write(f'   Ubicaciones: {Ubicacion.objects.count()}')
        self.stdout.write(f'   Animales vivos: {Animal.objects.filter(estado_vital="VIVO").count()}')
        self.stdout.write(f'   Animales en baja: {Animal.objects.filter(estado_vital="BAJA").count()}')
        self.stdout.write(f'   Partos registrados: {Parto.objects.count()}')
        self.stdout.write(f'   Incidencias activas: {Incidencia.objects.filter(resuelta=False).count()}')
        self.stdout.write('\n  Para probar las alertas:')
        self.stdout.write('  - RN-03 (Alerta Amarilla): Crear un animal con crotal "9999"')
        self.stdout.write('  - RN-04 (Alerta Roja): Ver incidencias -> Crotal 1002')
        self.stdout.write('-' * 50)
