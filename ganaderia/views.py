"""
Vistas de la aplicación Gestión Ganadera.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from .models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia
from .forms import (
    ExplotacionSetupForm, AnimalForm, AnimalBajaForm,
    PartoForm, TrasladoForm
)
from .services.animal_services import (
    validar_crotal, validar_intervalo_parto,
    registrar_parto, trasladar_animal, dar_de_baja_animal
)


# ─────────────────────────────────────────
# ONBOARDING WIZARD
# ─────────────────────────────────────────

def setup_wizard(request):
    """Wizard de configuración inicial (primer inicio)."""
    # Si ya existe una explotación, redirigir al home
    if Explotacion.objects.exists():
        return redirect('home')
    
    form = ExplotacionSetupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        explotacion = form.save()
        messages.success(
            request,
            f'✅ Explotación "{explotacion.nombre}" creada correctamente. ¡Bienvenido!'
        )
        return redirect('home')
    
    return render(request, 'ganaderia/setup_wizard.html', {'form': form})


# ─────────────────────────────────────────
# HOME / DASHBOARD
# ─────────────────────────────────────────

def home(request):
    """Panel de control principal."""
    explotacion = Explotacion.objects.first()
    
    # Estadísticas
    animales_vivos = Animal.objects.filter(estado_vital='VIVO')
    stats = {
        'total_animales': animales_vivos.count(),
        'hembras': animales_vivos.filter(sexo='H').count(),
        'machos': animales_vivos.filter(sexo='M').count(),
        'incidencias': Incidencia.objects.filter(resuelta=False).count(),
    }
    
    # Incidencias recientes (últimas 5)
    incidencias_recientes = Incidencia.objects.filter(
        resuelta=False
    ).select_related('animal')[:5]
    
    return render(request, 'ganaderia/home.html', {
        'explotacion': explotacion,
        'stats': stats,
        'incidencias_recientes': incidencias_recientes,
    })


# ─────────────────────────────────────────
# ANIMALES
# ─────────────────────────────────────────

def animal_list(request):
    """Listado de animales con filtros."""
    animales = Animal.objects.select_related(
        'finca_actual', 'sub_ubicacion', 'madre'
    ).order_by('crotal')
    
    # Filtros
    sexo = request.GET.get('sexo')
    estado = request.GET.get('estado', 'VIVO')
    finca_id = request.GET.get('finca')
    ubicacion_tipo = request.GET.get('ubicacion')
    
    if sexo:
        animales = animales.filter(sexo=sexo)
    if estado:
        animales = animales.filter(estado_vital=estado)
    if finca_id:
        animales = animales.filter(finca_actual_id=finca_id)
    if ubicacion_tipo:
        animales = animales.filter(sub_ubicacion__tipo=ubicacion_tipo)
    
    fincas = Finca.objects.all()
    
    return render(request, 'ganaderia/animal_list.html', {
        'animales': animales,
        'fincas': fincas,
        'filtros': {
            'sexo': sexo,
            'estado': estado,
            'finca_id': finca_id,
            'ubicacion_tipo': ubicacion_tipo,
        }
    })


def animal_detail(request, pk):
    """Ficha detallada de un animal."""
    animal = get_object_or_404(
        Animal.objects.select_related('finca_actual', 'sub_ubicacion', 'madre'),
        pk=pk
    )
    partos = animal.partos_como_madre.select_related('cria').all()
    incidencias = animal.incidencias.all()
    
    return render(request, 'ganaderia/animal_detail.html', {
        'animal': animal,
        'partos': partos,
        'incidencias': incidencias,
    })


def animal_create(request):
    """Crear un nuevo animal."""
    form = AnimalForm(request.POST or None)
    alerta_amarilla = None
    
    if request.method == 'POST' and form.is_valid():
        alerta_amarilla = getattr(form, '_crotal_alerta', None)
        
        # Si hay alerta amarilla, requiere confirmación explícita
        if alerta_amarilla == 'AMARILLO' and not request.POST.get('confirmar_crotal_historico'):
            return render(request, 'ganaderia/animal_form.html', {
                'form': form,
                'alerta_amarilla': True,
                'mensaje_alerta': getattr(form, '_crotal_mensaje', ''),
            })
        
        animal = form.save()
        
        # Crear incidencia si se usó crotal histórico
        if alerta_amarilla == 'AMARILLO':
            Incidencia.objects.create(
                animal=animal,
                tipo='AMARILLO',
                descripcion=(
                    f'Se reutilizó el crotal {animal.crotal} previamente asignado '
                    f'a un animal dado de baja.'
                )
            )
            messages.warning(
                request,
                f'⚠️ Animal {animal.crotal} creado con aviso: crotal histórico reutilizado.'
            )
        else:
            messages.success(request, f'✅ Animal {animal.crotal} registrado correctamente.')
        
        return redirect('animal_detail', pk=animal.pk)
    
    return render(request, 'ganaderia/animal_form.html', {'form': form, 'accion': 'Crear'})


def animal_baja(request, pk):
    """Dar de baja un animal."""
    animal = get_object_or_404(Animal, pk=pk, estado_vital='VIVO')
    form = AnimalBajaForm(request.POST or None)
    
    if request.method == 'POST' and form.is_valid():
        motivo = form.cleaned_data.get('motivo', '')
        dar_de_baja_animal(animal, motivo)
        messages.success(request, f'Animal {animal.crotal} dado de baja correctamente.')
        return redirect('animal_list')
    
    return render(request, 'ganaderia/animal_baja.html', {'animal': animal, 'form': form})


def animal_traslado(request, pk):
    """Trasladar un animal a otra finca/ubicación."""
    animal = get_object_or_404(Animal, pk=pk, estado_vital='VIVO')
    form = TrasladoForm(request.POST or None)
    advertencia_reproductora = False
    
    if request.method == 'POST' and form.is_valid():
        nueva_finca = form.cleaned_data['finca_destino']
        nueva_ubicacion = form.cleaned_data['ubicacion_destino']
        confirmar_reproductora = form.cleaned_data.get('confirmar_reproductora', False)
        
        # RN-05: Advertencia si reproductora va al cebadero
        if (
            animal.sexo == 'H' and
            animal.es_reproductora and
            nueva_ubicacion.tipo == 'CEBADERO' and
            not confirmar_reproductora
        ):
            return render(request, 'ganaderia/animal_traslado.html', {
                'animal': animal,
                'form': form,
                'advertencia_reproductora': True,
            })
        
        trasladar_animal(animal, nueva_finca, nueva_ubicacion)
        messages.success(
            request,
            f'✅ Animal {animal.crotal} trasladado a {nueva_finca.nombre} ({nueva_ubicacion.get_tipo_display()}).'
        )
        return redirect('animal_detail', pk=animal.pk)
    
    return render(request, 'ganaderia/animal_traslado.html', {
        'animal': animal,
        'form': form,
        'advertencia_reproductora': advertencia_reproductora,
    })


# ─────────────────────────────────────────
# PARTOS
# ─────────────────────────────────────────

def parto_create(request):
    """Registrar un nuevo parto."""
    form = PartoForm(request.POST or None)
    validacion_intervalo = None
    
    if request.method == 'POST' and form.is_valid():
        madre = form.cleaned_data['madre']
        fecha_parto = form.cleaned_data['fecha_parto']
        forzar = form.cleaned_data.get('forzar_guardado', False)
        observaciones = form.cleaned_data.get('observaciones', '')
        
        # Validar intervalo
        validacion = validar_intervalo_parto(madre, fecha_parto)
        
        if not validacion['valido'] and not forzar:
            # Mostrar advertencia roja y pedir confirmación
            return render(request, 'ganaderia/parto_form.html', {
                'form': form,
                'alerta_intervalo': True,
                'validacion': validacion,
            })
        
        # Preparar datos de la cría si se marcó el checkbox
        datos_cria = None
        if form.cleaned_data.get('registrar_cria') and form.cleaned_data.get('crotal_cria'):
            datos_cria = {
                'crotal': form.cleaned_data['crotal_cria'],
                'sexo': form.cleaned_data['sexo_cria'],
                'raza': form.cleaned_data.get('raza_cria', madre.raza),
            }
        
        try:
            resultado = registrar_parto(
                madre=madre,
                fecha_parto=fecha_parto,
                forzar=forzar,
                observaciones=observaciones,
                datos_cria=datos_cria,
            )
            
            if resultado['alerta'] == 'ROJO':
                messages.warning(
                    request,
                    f'⚠️ Parto registrado con alerta de intervalo. '
                    f'Incidencia RN-04 generada.'
                )
            else:
                messages.success(request, '✅ Parto registrado correctamente.')
            
            return redirect('animal_detail', pk=madre.pk)
        
        except Exception as e:
            messages.error(request, f'Error al registrar el parto: {str(e)}')
    
    return render(request, 'ganaderia/parto_form.html', {
        'form': form,
        'validacion_intervalo': validacion_intervalo,
    })


# ─────────────────────────────────────────
# FINCAS
# ─────────────────────────────────────────

def finca_list(request):
    """Listado de fincas con sus ubicaciones y animales."""
    fincas = Finca.objects.prefetch_related(
        'ubicaciones__animales'
    ).select_related('explotacion').all()
    
    return render(request, 'ganaderia/finca_list.html', {'fincas': fincas})


# ─────────────────────────────────────────
# INCIDENCIAS
# ─────────────────────────────────────────

def incidencias(request):
    """Listado de incidencias activas."""
    incidencias_activas = Incidencia.objects.filter(
        resuelta=False
    ).select_related('animal', 'parto').order_by('-created_at')
    
    incidencias_resueltas = Incidencia.objects.filter(
        resuelta=True
    ).select_related('animal', 'parto').order_by('-created_at')[:20]
    
    return render(request, 'ganaderia/incidencias.html', {
        'incidencias_activas': incidencias_activas,
        'incidencias_resueltas': incidencias_resueltas,
    })


def incidencia_resolver(request, pk):
    """Marcar incidencia como resuelta."""
    incidencia = get_object_or_404(Incidencia, pk=pk)
    if request.method == 'POST':
        incidencia.resuelta = True
        incidencia.save()
        messages.success(request, 'Incidencia marcada como resuelta.')
    return redirect('incidencias')


# ─────────────────────────────────────────
# AJAX / HTMX
# ─────────────────────────────────────────

def api_validar_crotal(request):
    """Endpoint HTMX/AJAX para validar crotal en tiempo real."""
    crotal = request.GET.get('crotal', '')
    resultado = validar_crotal(crotal)
    return JsonResponse(resultado)


def api_ubicaciones_por_finca(request):
    """Endpoint AJAX para obtener ubicaciones filtradas por finca."""
    finca_id = request.GET.get('finca_id')
    ubicaciones = []
    if finca_id:
        ubicaciones = list(
            Ubicacion.objects.filter(finca_id=finca_id).values('id', 'tipo')
        )
        # Añadir el display del tipo
        tipo_map = dict(Ubicacion.TIPO_CHOICES)
        for u in ubicaciones:
            u['tipo_display'] = tipo_map.get(u['tipo'], u['tipo'])
    return JsonResponse({'ubicaciones': ubicaciones})
