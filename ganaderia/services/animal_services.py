"""
Servicios de lógica de negocio para animales y partos.
Aísla las reglas de negocio de las vistas.
"""
from datetime import date, timedelta
from django.db import transaction
from django.core.exceptions import ValidationError
from ..models import Animal, Parto, Incidencia, Ubicacion

# Constante de negocio: intervalo mínimo entre partos (días)
INTERVALO_MINIMO_PARTOS = 270


def validar_crotal(crotal: str, excluir_pk: int = None) -> dict:
    """
    Valida un crotal antes de asignarlo a un animal.
    
    Reglas:
    - RN-01: Debe ser exactamente 4 dígitos numéricos.
    - RN-02: No puede estar en uso por un animal VIVO.
    - RN-03: Si existe en BAJA, emite Alerta Amarilla (reutilización histórica).
    
    Returns:
        dict con keys:
        - 'valido': bool
        - 'bloqueante': bool (si True, no se puede continuar)
        - 'alerta': str|None ('AMARILLO' si hay reutilización histórica)
        - 'mensaje': str con la descripción
        - 'animal_baja': Animal|None (animal en baja con ese crotal, si existe)
    """
    # RN-01: Formato
    if not crotal or not crotal.isdigit() or len(crotal) != 4:
        return {
            'valido': False,
            'bloqueante': True,
            'alerta': None,
            'mensaje': 'RN-01: El crotal debe tener exactamente 4 dígitos numéricos.',
            'animal_baja': None,
        }
    
    # Buscar animal VIVO con este crotal
    qs_vivo = Animal.objects.filter(crotal=crotal, estado_vital='VIVO')
    if excluir_pk:
        qs_vivo = qs_vivo.exclude(pk=excluir_pk)
    
    if qs_vivo.exists():
        # RN-02: Crotal activo en uso
        return {
            'valido': False,
            'bloqueante': True,
            'alerta': None,
            'mensaje': f'RN-02: El crotal {crotal} ya está en uso por un animal activo.',
            'animal_baja': None,
        }
    
    # Buscar animal en BAJA con este crotal
    qs_baja = Animal.objects.filter(crotal=crotal, estado_vital='BAJA')
    animal_baja = qs_baja.last()
    
    if animal_baja:
        # RN-03: Alerta Amarilla - crotal histórico
        return {
            'valido': True,
            'bloqueante': False,
            'alerta': 'AMARILLO',
            'mensaje': (
                f'RN-03: El crotal {crotal} fue usado por un animal dado de baja '
                f'el {animal_baja.fecha_baja}. ¿Desea asignar igualmente?'
            ),
            'animal_baja': animal_baja,
        }
    
    # Crotal nuevo y disponible
    return {
        'valido': True,
        'bloqueante': False,
        'alerta': None,
        'mensaje': 'Crotal disponible.',
        'animal_baja': None,
    }


def validar_intervalo_parto(madre: Animal, fecha_nuevo_parto: date) -> dict:
    """
    Valida el intervalo entre partos de una misma madre.
    
    Regla RN-04: El intervalo mínimo biológico es de 270 días (~9 meses).
    Si es menor, se genera Alerta Roja. El usuario puede forzar el guardado.
    
    Returns:
        dict con keys:
        - 'valido': bool (puede guardarse, pero con alerta si False)
        - 'alerta': str|None ('ROJO' si intervalo < 270 días)
        - 'dias_intervalo': int|None
        - 'parto_anterior': Parto|None
        - 'mensaje': str
    """
    partos_anteriores = Parto.objects.filter(
        madre=madre
    ).order_by('-fecha_parto')
    
    parto_anterior = partos_anteriores.first()
    
    if not parto_anterior:
        return {
            'valido': True,
            'alerta': None,
            'dias_intervalo': None,
            'parto_anterior': None,
            'mensaje': 'Primer parto de esta madre. Sin restricciones de intervalo.',
        }
    
    dias_intervalo = (fecha_nuevo_parto - parto_anterior.fecha_parto).days
    
    if dias_intervalo < INTERVALO_MINIMO_PARTOS:
        return {
            'valido': False,
            'alerta': 'ROJO',
            'dias_intervalo': dias_intervalo,
            'parto_anterior': parto_anterior,
            'mensaje': (
                f'RN-04: Intervalo entre partos de {dias_intervalo} días, '
                f'inferior al mínimo reglamentario de {INTERVALO_MINIMO_PARTOS} días (~9 meses). '
                f'Conflicto con normativa Diputación. ¿Desea forzar el guardado?'
            ),
        }
    
    return {
        'valido': True,
        'alerta': None,
        'dias_intervalo': dias_intervalo,
        'parto_anterior': parto_anterior,
        'mensaje': f'Intervalo correcto: {dias_intervalo} días desde el parto anterior.',
    }


@transaction.atomic
def registrar_parto(
    madre: Animal,
    fecha_parto: date,
    forzar: bool = False,
    observaciones: str = '',
    datos_cria: dict = None
) -> dict:
    """
    Registra un parto en la base de datos.
    
    Si datos_cria no es None, también crea el animal cría.
    Si hay alerta de intervalo y forzar=True, guarda con alerta_intervalo=True.
    
    Returns:
        dict con 'parto', 'cria', 'incidencia_creada', 'alerta'
    """
    # Validar intervalo
    validacion = validar_intervalo_parto(madre, fecha_parto)
    
    if not validacion['valido'] and not forzar:
        raise ValidationError(validacion['mensaje'])
    
    alerta_intervalo = validacion['alerta'] == 'ROJO'
    
    # Crear animal cría si se proporcionan datos
    cria = None
    if datos_cria:
        cria = Animal.objects.create(
            crotal=datos_cria['crotal'],
            sexo=datos_cria['sexo'],
            raza=datos_cria.get('raza', madre.raza),
            fecha_nacimiento=fecha_parto,
            estado_vital='VIVO',
            finca_actual=madre.finca_actual,
            sub_ubicacion=madre.sub_ubicacion,
            madre=madre,
        )
    
    # Crear el parto
    parto = Parto.objects.create(
        madre=madre,
        fecha_parto=fecha_parto,
        cria=cria,
        alerta_intervalo=alerta_intervalo,
        observaciones=observaciones,
    )
    
    # Crear incidencia si hay alerta roja
    incidencia = None
    if alerta_intervalo:
        incidencia = Incidencia.objects.create(
            animal=madre,
            tipo='ROJO',
            descripcion=(
                f'Intervalo entre partos de {validacion["dias_intervalo"]} días '
                f'(mínimo: {INTERVALO_MINIMO_PARTOS} días). '
                f'Parto anterior: {validacion["parto_anterior"].fecha_parto}.'
            ),
            parto=parto,
        )
    
    return {
        'parto': parto,
        'cria': cria,
        'incidencia_creada': incidencia,
        'alerta': validacion['alerta'],
    }


@transaction.atomic
def trasladar_animal(animal: Animal, nueva_finca, nueva_ubicacion: Ubicacion) -> Animal:
    """
    Traslada un animal a una nueva finca/ubicación.
    
    Lógica según diagrama de secuencia 001:
    - Si nueva_ubicacion.tipo == 'CEBADERO':
        asigna fecha_entrada_cebadero = hoy
    - Si nueva_ubicacion.tipo in ('PASTO', 'APARTADO'):
        limpia fecha_entrada_cebadero = None
    
    Emite advertencia (no bloqueante) si se traslada una reproductora al cebadero.
    """
    # Advertencia RN-05: hembra reproductora al cebadero
    es_reproductora_al_cebadero = (
        animal.sexo == 'H' and
        animal.es_reproductora and
        nueva_ubicacion.tipo == 'CEBADERO'
    )
    
    animal.finca_actual = nueva_finca
    animal.sub_ubicacion = nueva_ubicacion
    
    if nueva_ubicacion.tipo == 'CEBADERO':
        animal.fecha_entrada_cebadero = date.today()
    else:
        animal.fecha_entrada_cebadero = None
    
    animal.save()
    
    return animal


@transaction.atomic
def dar_de_baja_animal(animal: Animal, motivo: str = '') -> Animal:
    """
    Da de baja un animal (estado_vital = 'BAJA').
    Las bajas son directas, sin papelera de 7 días.
    """
    animal.estado_vital = 'BAJA'
    animal.fecha_baja = date.today()
    animal.motivo_baja = motivo
    animal.save()
    return animal
