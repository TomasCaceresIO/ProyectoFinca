"""
Servicios de lógica de negocio para animales y partos.
"""
from datetime import date
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from ..models import Animal, Parto, Incidencia, Finca

INTERVALO_MINIMO_PARTOS = 270


def validar_crotal(crotal: str, excluir_pk: int = None) -> dict:
    """
    Valida un crotal antes de asignarlo a un animal.
    
    Reglas:
    - RN-01: Debe ser exactamente 4 dígitos numéricos.
    - RN-02: No puede estar en uso por un animal VIVO.
    - RN-03: Si existe en BAJA, emite Alerta Amarilla (reutilización histórica).
    """
    if not crotal or not crotal.isdigit() or len(crotal) != 4:
        return {
            'valido': False,
            'bloqueante': True,
            'alerta': None,
            'mensaje': 'RN-01: El crotal debe tener exactamente 4 dígitos numéricos.',
            'animal_baja': None,
        }
    
    qs_vivo = Animal.objects.filter(crotal=crotal, estado_vital='VIVO')
    if excluir_pk:
        qs_vivo = qs_vivo.exclude(pk=excluir_pk)
    
    if qs_vivo.exists():
        return {
            'valido': False,
            'bloqueante': True,
            'alerta': None,
            'mensaje': f'RN-02: El crotal {crotal} ya está en uso por un animal activo.',
            'animal_baja': None,
        }
    
    qs_baja = Animal.objects.filter(crotal=crotal, estado_vital='BAJA')
    animal_baja = qs_baja.last()
    
    if animal_baja:
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
    
    return {
        'valido': True,
        'bloqueante': False,
        'alerta': None,
        'mensaje': 'Crotal disponible.',
        'animal_baja': None,
    }


def validar_intervalo_parto(madre: Animal, fecha_nuevo_parto: date, excluir_parto_id: int = None) -> dict:
    """
    Valida el intervalo entre partos de una misma madre bidireccionalmente
    (tanto con el parto anterior más cercano como con el posterior más cercano).
    
    Regla RN-04: Mínimo 270 días.
    """
    partos = Parto.objects.filter(madre=madre)
    if excluir_parto_id:
        partos = partos.exclude(pk=excluir_parto_id)

    parto_anterior = partos.filter(fecha_parto__lte=fecha_nuevo_parto).order_by('-fecha_parto').first()
    parto_posterior = partos.filter(fecha_parto__gte=fecha_nuevo_parto).order_by('fecha_parto').first()

    alerta = False
    dias_anterior = None
    dias_posterior = None

    if parto_anterior:
        dias_anterior = (fecha_nuevo_parto - parto_anterior.fecha_parto).days
        if dias_anterior < INTERVALO_MINIMO_PARTOS:
            alerta = True

    if parto_posterior:
        dias_posterior = (parto_posterior.fecha_parto - fecha_nuevo_parto).days
        if dias_posterior < INTERVALO_MINIMO_PARTOS:
            alerta = True

    if alerta:
        dias_conflicto = dias_anterior if (dias_anterior is not None and dias_anterior < 270) else dias_posterior
        return {
            'valido': False,
            'alerta': 'ROJO',
            'dias_intervalo': dias_conflicto,
            'dias_anterior': dias_anterior,
            'dias_posterior': dias_posterior,
            'parto_anterior': parto_anterior,
            'parto_posterior': parto_posterior,
            'mensaje': (
                f'RN-04: Intervalo entre partos inferior al mínimo reglamentario de 270 días (~9 meses). '
                f'Conflicto detectado.'
            ),
        }

    return {
        'valido': True,
        'alerta': None,
        'dias_intervalo': dias_anterior,
        'dias_anterior': dias_anterior,
        'dias_posterior': dias_posterior,
        'parto_anterior': parto_anterior,
        'parto_posterior': parto_posterior,
        'mensaje': 'Intervalo correcto.',
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
    """
    validacion = validar_intervalo_parto(madre, fecha_parto)
    if not validacion['valido'] and not forzar:
        raise ValidationError(validacion['mensaje'])

    alerta_intervalo = validacion['alerta'] == 'ROJO'

    cria = None
    if datos_cria and datos_cria.get('crotal'):
        cria = Animal.objects.create(
            crotal=datos_cria['crotal'],
            sexo=datos_cria.get('sexo', 'M'),
            raza=datos_cria.get('raza', madre.raza),
            fecha_nacimiento=fecha_parto,
            estado_vital='VIVO',
            finca=madre.finca,
            sub_ubicacion=madre.sub_ubicacion,
            madre=madre,
        )

    parto = Parto.objects.create(
        madre=madre,
        fecha_parto=fecha_parto,
        cria=cria,
        alerta_intervalo=alerta_intervalo,
        observaciones=observaciones,
    )

    incidencia = None
    if alerta_intervalo:
        incidencia = Incidencia.objects.create(
            animal=madre,
            tipo='ROJO',
            descripcion=(
                f'Intervalo entre partos de {validacion["dias_intervalo"]} días '
                f'(mínimo: {INTERVALO_MINIMO_PARTOS} días).'
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
def actualizar_parto(parto: Parto, nueva_fecha_parto: date, observaciones: str = None) -> Parto:
    """
    Modifica la fecha de un parto existente y recalcula sus alertas de intervalo.
    """
    validacion = validar_intervalo_parto(parto.madre, nueva_fecha_parto, excluir_parto_id=parto.pk)
    parto.fecha_parto = nueva_fecha_parto
    if observaciones is not None:
        parto.observaciones = observaciones
    
    parto.alerta_intervalo = (validacion['alerta'] == 'ROJO')
    parto.save()

    # Gestionar incidencia asociada
    incidencias = Incidencia.objects.filter(parto=parto)
    if parto.alerta_intervalo:
        if not incidencias.exists():
            Incidencia.objects.create(
                animal=parto.madre,
                tipo='ROJO',
                descripcion=f'Intervalo de parto recalculado (< {INTERVALO_MINIMO_PARTOS} días).',
                parto=parto,
            )
        else:
            incidencias.update(
                resuelta=False,
                descripcion=f'Intervalo entre partos: {validacion["dias_intervalo"]} días (mínimo: 270d).'
            )
    else:
        incidencias.update(resuelta=True)

    return parto


@transaction.atomic
def trasladar_animal(animal: Animal, nueva_finca: Finca, nueva_sub_ubicacion: str) -> Animal:
    """
    Traslada un animal a una nueva finca/sub_ubicación.
    - Cebadero: sella fecha_entrada_cebadero = hoy
    - Pasto / Apartado: fecha_entrada_cebadero = None
    """
    animal.finca = nueva_finca
    animal.sub_ubicacion = nueva_sub_ubicacion
    if nueva_sub_ubicacion == 'CEBADERO':
        animal.fecha_entrada_cebadero = timezone.now().date()
    else:
        animal.fecha_entrada_cebadero = None
    animal.save()
    return animal


@transaction.atomic
def dar_de_baja_animal(animal: Animal, motivo: str = '') -> Animal:
    """
    Da de baja un animal (estado_vital = 'BAJA').
    Sus partos e hijos permanecen intactos.
    """
    animal.estado_vital = 'BAJA'
    animal.fecha_baja = timezone.now().date()
    animal.motivo_baja = motivo
    animal.save()
    return animal
