"""
Vistas de la aplicación Gestión Ganadera.
"""
import csv
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from datetime import date

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse, Http404
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Max
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST


def healthcheck(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
        return HttpResponse("OK", status=200, content_type="text/plain")
    except Exception as e:
        return HttpResponse(f"UNHEALTHY: {str(e)}", status=503, content_type="text/plain")

from .models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia
from .forms import (
    ExplotacionSetupForm, ExplotacionForm, FincaForm, AnimalForm, AnimalBajaForm,
    PartoForm, PartoEditForm, TrasladoForm
)
from .services.animal_services import (
    validar_crotal, validar_intervalo_parto,
    registrar_parto, actualizar_parto, trasladar_animal, dar_de_baja_animal
)


def get_filtered_animales(request, with_annotations=False):
    """Helper que aplica los mismos filtros multifactor que la tabla principal."""
    animales = Animal.objects.filter(estado_vital='VIVO').select_related('finca', 'madre')
    
    if with_annotations:
        animales = animales.annotate(
            total_partos=Count('partos_como_madre'),
            ultimo_parto_fecha=Max('partos_como_madre__fecha_parto')
        )
    else:
        animales = animales.prefetch_related('partos_como_madre')
    
    finca_id = request.GET.get('finca')
    recinto = request.GET.get('recinto')
    sexo = request.GET.get('sexo')
    raza = request.GET.get('raza')
    orden = request.GET.get('orden', 'crotal')
    
    if finca_id:
        animales = animales.filter(finca_id=finca_id)
    if recinto:
        animales = animales.filter(sub_ubicacion=recinto)
    if sexo:
        animales = animales.filter(sexo=sexo)
    if raza:
        animales = animales.filter(raza=raza)
        
    lista = list(animales)
    if orden == 'dias_parto_desc':
        today = date.today()
        def calc_dias(a):
            if with_annotations:
                if a.sexo == 'H' and getattr(a, 'ultimo_parto_fecha', None):
                    return (today - a.ultimo_parto_fecha).days
                return -1
            return a.dias_desde_ultimo_parto if a.dias_desde_ultimo_parto is not None else -1

        lista.sort(key=calc_dias, reverse=True)
    elif orden == 'edad_desc':
        lista.sort(key=lambda a: a.fecha_nacimiento)
    else:
        lista.sort(key=lambda a: a.crotal)
        
    return lista


@login_required
def setup_wizard(request):
    """Wizard de configuración inicial (Onboarding)."""
    if Explotacion.objects.exists():
        return redirect('home')
    
    form = ExplotacionSetupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        nombre_exp = form.cleaned_data['nombre']
        rega = form.cleaned_data['codigo_rega']
        nombre_finca = form.cleaned_data['nombre_finca']
        crear_cebadero = form.cleaned_data.get('cebadero', False)
        crear_apartado = form.cleaned_data.get('apartado', False)
        
        explotacion = Explotacion.objects.create(nombre=nombre_exp, codigo_rega=rega)
        finca = Finca.objects.create(explotacion=explotacion, nombre=nombre_finca)
        
        Ubicacion.objects.create(finca=finca, tipo_ubicacion='PASTO')
        if crear_cebadero:
            Ubicacion.objects.create(finca=finca, tipo_ubicacion='CEBADERO')
        if crear_apartado:
            Ubicacion.objects.create(finca=finca, tipo_ubicacion='APARTADO')
            
        messages.success(request, f'✅ Explotación "{explotacion.nombre}" creada correctamente. ¡Bienvenido!')
        return redirect('home')
    
    return render(request, 'ganaderia/setup_wizard.html', {'form': form})


@login_required
def explotacion_edit(request):
    """Edición de los datos oficiales de la explotación (Nombre y Código REGA)."""
    explotacion = Explotacion.objects.first()
    if not explotacion:
        return redirect('setup_wizard')
        
    form = ExplotacionForm(request.POST or None, instance=explotacion)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, '✅ Configuración de la Explotación actualizada correctamente.')
        return redirect('home')
        
    return render(request, 'ganaderia/explotacion_form.html', {'form': form, 'explotacion': explotacion})


@login_required
def home(request):
    """Panel de control principal y Censo Activo (Home Data Grid)."""
    explotacion = Explotacion.objects.first()
    
    animales_vivos = Animal.objects.filter(estado_vital='VIVO').select_related(
        'finca', 'madre'
    ).prefetch_related('partos_como_madre')
    
    stats = {
        'total_animales': animales_vivos.count(),
        'hembras': animales_vivos.filter(sexo='H').count(),
        'machos': animales_vivos.filter(sexo='M').count(),
        'incidencias': Parto.objects.filter(alerta_intervalo=True, madre__estado_vital='VIVO').count(),
    }
    
    finca_id = request.GET.get('finca')
    recinto = request.GET.get('recinto')
    sexo = request.GET.get('sexo')
    raza = request.GET.get('raza')
    orden = request.GET.get('orden', 'crotal')
    
    lista_animales = get_filtered_animales(request)
    paginator = Paginator(lista_animales, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    fincas = Finca.objects.all()
    incidencias_recientes = Incidencia.objects.filter(
        resuelta=False,
        animal__estado_vital='VIVO',
        parto__alerta_intervalo=True,
        parto__madre__estado_vital='VIVO'
    ).select_related('animal', 'parto')[:5]
    
    context = {
        'explotacion': explotacion,
        'stats': stats,
        'animales': page_obj.object_list,
        'page_obj': page_obj,
        'paginator': paginator,
        'fincas': fincas,
        'incidencias_recientes': incidencias_recientes,
        'filtros': {
            'finca_id': finca_id,
            'recinto': recinto,
            'sexo': sexo,
            'raza': raza,
            'orden': orden,
        }
    }
    
    if request.headers.get('HX-Request') or request.GET.get('partial'):
        return render(request, 'ganaderia/partials/animal_grid_tbody.html', context)
        
    return render(request, 'ganaderia/home.html', context)


@login_required
def animal_list(request):
    """Redirige al Data Grid principal en Home."""
    return redirect('home')


@login_required
def animal_detail(request, crotal):
    """
    Ficha detallada de un animal.
    Búsqueda defensiva ante crotales históricos duplicados (prioriza VIVO, luego el último en BAJA).
    """
    animal = (
        Animal.objects.filter(crotal=crotal, estado_vital='VIVO')
        .select_related('finca', 'madre')
        .prefetch_related('partos_como_madre__cria')
        .first()
    )
    if not animal:
        animal = (
            Animal.objects.filter(crotal=crotal)
            .select_related('finca', 'madre')
            .prefetch_related('partos_como_madre__cria')
            .order_by('-id')
            .first()
        )
    if not animal:
        raise Http404('Animal no encontrado')

    partos = animal.partos_como_madre.select_related('cria', 'cria2').order_by('-fecha_parto')
    incidencias = animal.incidencias.all()
    edit_form = AnimalForm(instance=animal)
    
    return render(request, 'ganaderia/animal_detail.html', {
        'animal': animal,
        'partos': partos,
        'incidencias': incidencias,
        'edit_form': edit_form,
    })


@login_required
def animal_edit(request, crotal):
    """
    Editar atributos básicos de un animal desde su ficha.
    Aplica restricciones biológicas y de linaje.
    """
    animal = Animal.objects.filter(crotal=crotal, estado_vital='VIVO').first()
    if not animal:
        animal = Animal.objects.filter(crotal=crotal).order_by('-id').first()
    if not animal:
        raise Http404('Animal no encontrado')

    form = AnimalForm(request.POST or None, instance=animal)
    alerta_amarilla = None

    if request.method == 'POST':
        if form.is_valid():
            alerta_amarilla = getattr(form, '_crotal_alerta', None)
            if alerta_amarilla == 'AMARILLO' and not request.POST.get('confirmar_crotal_historico'):
                animal.refresh_from_db()
                partos = animal.partos_como_madre.select_related('cria', 'cria2').order_by('-fecha_parto')
                return render(request, 'ganaderia/animal_detail.html', {
                    'animal': animal,
                    'partos': partos,
                    'incidencias': animal.incidencias.all(),
                    'edit_form': form,
                    'alerta_amarilla_edit': True,
                    'mensaje_alerta_edit': getattr(form, '_crotal_mensaje', ''),
                    'open_edit_modal': True,
                })

            with transaction.atomic():
                animal_actualizado = form.save()

                if alerta_amarilla == 'AMARILLO':
                    Incidencia.objects.create(
                        animal=animal_actualizado,
                        tipo='AMARILLO',
                        descripcion=f'Se reutilizó el crotal {animal_actualizado.crotal} previamente asignado a un animal en baja.'
                    )
                    messages.warning(request, f'⚠️ Animal {animal_actualizado.crotal} actualizado con aviso de crotal histórico.')
                else:
                    messages.success(request, f'✅ Datos del animal {animal_actualizado.crotal} actualizados correctamente.')

            return redirect('animal_detail', crotal=animal_actualizado.crotal)

        else:
            animal.refresh_from_db()
            partos = animal.partos_como_madre.select_related('cria', 'cria2').order_by('-fecha_parto')
            return render(request, 'ganaderia/animal_detail.html', {
                'animal': animal,
                'partos': partos,
                'incidencias': animal.incidencias.all(),
                'edit_form': form,
                'open_edit_modal': True,
            })

    return redirect('animal_detail', crotal=animal.crotal)


@login_required
def animal_create(request):
    """Crear un nuevo animal."""
    form = AnimalForm(request.POST or None)
    alerta_amarilla = None
    
    if request.method == 'POST' and form.is_valid():
        alerta_amarilla = getattr(form, '_crotal_alerta', None)
        
        if alerta_amarilla == 'AMARILLO' and not request.POST.get('confirmar_crotal_historico'):
            return render(request, 'ganaderia/animal_form.html', {
                'form': form,
                'alerta_amarilla': True,
                'mensaje_alerta': getattr(form, '_crotal_mensaje', ''),
            })
        
        animal = form.save()
        
        if alerta_amarilla == 'AMARILLO':
            Incidencia.objects.create(
                animal=animal,
                tipo='AMARILLO',
                descripcion=f'Se reutilizó el crotal {animal.crotal} previamente asignado a un animal en baja.'
            )
            messages.warning(request, f'⚠️ Animal {animal.crotal} creado con aviso: crotal histórico reutilizado.')
        else:
            messages.success(request, f'✅ Animal {animal.crotal} registrado correctamente.')
        
        return redirect('animal_detail', crotal=animal.crotal)
    
    return render(request, 'ganaderia/animal_form.html', {'form': form, 'accion': 'Crear'})


@login_required
def animal_baja(request, crotal):
    """Dar de baja un animal (defensivo ante crotales históricos)."""
    animal = Animal.objects.filter(crotal=crotal, estado_vital='VIVO').first()
    if not animal:
        raise Http404('Animal no encontrado o ya en estado BAJA')
        
    form = AnimalBajaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        motivo = form.cleaned_data.get('motivo', '')
        dar_de_baja_animal(animal, motivo)
        messages.success(request, f'Animal {animal.crotal} dado de baja correctamente.')
        return redirect('home')
    
    return render(request, 'ganaderia/animal_baja.html', {'animal': animal, 'form': form})


@login_required
def animal_traslado(request, crotal):
    """Trasladar un animal a otra finca/sub_ubicación (defensivo ante crotales históricos)."""
    animal = Animal.objects.filter(crotal=crotal, estado_vital='VIVO').first()
    if not animal:
        raise Http404('Animal no encontrado o ya en estado BAJA')
        
    form = TrasladoForm(request.POST or None)
    advertencia_reproductora = False
    
    if request.method == 'POST' and form.is_valid():
        nueva_finca = form.cleaned_data['finca_destino']
        nueva_ubicacion = form.cleaned_data['ubicacion_destino']
        confirmar_reproductora = form.cleaned_data.get('confirmar_reproductora', False)
        
        if (
            animal.sexo == 'H' and
            animal.es_reproductora and
            nueva_ubicacion == 'CEBADERO' and
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
            f'✅ Animal {animal.crotal} trasladado a {nueva_finca.nombre} ({nueva_ubicacion}).'
        )
        return redirect('animal_detail', crotal=animal.crotal)
    
    return render(request, 'ganaderia/animal_traslado.html', {
        'animal': animal,
        'form': form,
        'advertencia_reproductora': advertencia_reproductora,
    })


@login_required
def parto_create(request):
    """Registrar un nuevo parto (1 o 2 crías obligatorias)."""
    madre_crotal = request.GET.get('madre')
    initial = {}
    if madre_crotal:
        madre = Animal.objects.filter(crotal=madre_crotal, sexo='H', estado_vital='VIVO').first()
        if madre:
            initial['madre'] = madre.pk

    form = PartoForm(request.POST or None, initial=initial)
    
    if request.method == 'POST' and form.is_valid():
        madre = form.cleaned_data['madre']
        fecha_parto = form.cleaned_data['fecha_parto']
        forzar = form.cleaned_data.get('forzar_guardado', False)
        observaciones = form.cleaned_data.get('observaciones', '')
        
        crias = [
            {
                'crotal': form.cleaned_data['crotal_cria_1'],
                'sexo': form.cleaned_data['sexo_cria_1'],
                'raza': form.cleaned_data.get('raza_cria_1') or madre.raza,
            }
        ]

        if form.cleaned_data.get('es_gemelar'):
            crias.append({
                'crotal': form.cleaned_data['crotal_cria_2'],
                'sexo': form.cleaned_data['sexo_cria_2'],
                'raza': form.cleaned_data.get('raza_cria_2') or madre.raza,
            })

        validacion = validar_intervalo_parto(madre, fecha_parto)
        
        if not validacion['valido'] and not forzar:
            return render(request, 'ganaderia/parto_form.html', {
                'form': form,
                'alerta_intervalo': True,
                'validacion': validacion,
            })

        try:
            resultado = registrar_parto(
                madre=madre,
                fecha_parto=fecha_parto,
                forzar=forzar,
                observaciones=observaciones,
                crias=crias,
            )
            
            if resultado['alerta'] == 'ROJO':
                messages.warning(request, f'⚠️ Parto registrado con alerta de intervalo < 270 días.')
            else:
                messages.success(request, '✅ Parto registrado correctamente.')
            
            return redirect('animal_detail', crotal=madre.crotal)
        except Exception as e:
            messages.error(request, f'Error al registrar el parto: {str(e)}')
    
    return render(request, 'ganaderia/parto_form.html', {'form': form})


@login_required
def parto_edit(request, pk):
    """Editar la fecha u observaciones de un parto existente (recalcula intervalo)."""
    parto = get_object_or_404(Parto.objects.select_related('madre'), pk=pk)
    form = PartoEditForm(request.POST or None, instance=parto)
    
    if request.method == 'POST' and form.is_valid():
        nueva_fecha = form.cleaned_data['fecha_parto']
        actualizar_parto(parto, nueva_fecha, form.cleaned_data.get('observaciones'))
        messages.success(request, '✅ Parto actualizado correctamente.')
        
        if request.headers.get('HX-Request'):
            return HttpResponse(status=200, headers={'HX-Refresh': 'true'})
        return redirect('animal_detail', crotal=parto.madre.crotal)
        
    return render(request, 'ganaderia/parto_edit.html', {'parto': parto, 'form': form})


@login_required
def finca_list(request):
    """Listado de fincas y sus recintos."""
    fincas = Finca.objects.prefetch_related('ubicaciones', 'animales').all()
    
    resumen_fincas = []
    for finca in fincas:
        animales_vivos = finca.animales.filter(estado_vital='VIVO')
        n_pasto = animales_vivos.filter(sub_ubicacion='PASTO').count()
        n_cebadero = animales_vivos.filter(sub_ubicacion='CEBADERO').count()
        n_apartado = animales_vivos.filter(sub_ubicacion='APARTADO').count()
        resumen_fincas.append({
            'finca': finca,
            'total_animales': animales_vivos.count(),
            'n_pasto': n_pasto,
            'n_cebadero': n_cebadero,
            'n_apartado': n_apartado,
        })
        
    return render(request, 'ganaderia/finca_list.html', {'resumen_fincas': resumen_fincas})


@login_required
def finca_create(request):
    """Crear una nueva finca con sus recintos."""
    explotacion = Explotacion.objects.first()
    if not explotacion:
        return redirect('setup_wizard')
        
    form = FincaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        nombre = form.cleaned_data['nombre']
        crear_cebadero = form.cleaned_data.get('cebadero', False)
        crear_apartado = form.cleaned_data.get('apartado', False)
        
        finca = Finca.objects.create(explotacion=explotacion, nombre=nombre)
        Ubicacion.objects.create(finca=finca, tipo_ubicacion='PASTO')
        if crear_cebadero:
            Ubicacion.objects.create(finca=finca, tipo_ubicacion='CEBADERO')
        if crear_apartado:
            Ubicacion.objects.create(finca=finca, tipo_ubicacion='APARTADO')
            
        messages.success(request, f'✅ Finca "{finca.nombre}" creada correctamente con sus recintos.')
        return redirect('finca_list')
        
    return render(request, 'ganaderia/finca_form.html', {'form': form})


@login_required
def finca_delete(request, pk):
    """
    Asistente de eliminación y vaciado de finca:
    - 0 animales vivos: elimina directamente.
    - Con animales vivos: opción A (traslado total), opción B (baja en bloque), opción C (selección granular).
    """
    finca = get_object_or_404(Finca, pk=pk)
    animales_vivos = list(finca.animales.filter(estado_vital='VIVO'))
    otras_fincas = Finca.objects.exclude(pk=finca.pk)
    active_option = 'opcion_a'
    
    if len(animales_vivos) == 0:
        if request.method == 'POST':
            with transaction.atomic():
                finca.delete()
            messages.success(request, f'✅ Finca "{finca.nombre}" eliminada correctamente.')
            return redirect('finca_list')
        return render(request, 'ganaderia/finca_delete_assistant.html', {
            'finca': finca,
            'animales_vivos': [],
            'otras_fincas': otras_fincas,
            'active_option': active_option,
        })

    if request.method == 'POST':
        opcion = request.POST.get('opcion')
        active_option = opcion or 'opcion_a'
        
        if opcion == 'opcion_a':
            destino_id = request.POST.get('finca_destino')
            if not destino_id:
                messages.error(request, '⚠️ Debes seleccionar una finca de destino para el traslado total.')
            else:
                finca_destino = get_object_or_404(Finca, pk=destino_id)
                with transaction.atomic():
                    for animal in animales_vivos:
                        trasladar_animal(animal, finca_destino, 'PASTO')
                    finca.delete()
                messages.success(request, f'✅ Finca "{finca.nombre}" eliminada. Todos sus animales fueron trasladados a {finca_destino.nombre}.')
                return redirect('finca_list')

        elif opcion == 'opcion_b':
            motivo = request.POST.get('motivo_baja', f'Vaciado por eliminación de finca {finca.nombre}')
            with transaction.atomic():
                for animal in animales_vivos:
                    dar_de_baja_animal(animal, motivo)
                finca.delete()
            messages.success(request, f'✅ Finca "{finca.nombre}" eliminada. Todos sus animales fueron dados de baja.')
            return redirect('finca_list')

        elif opcion == 'opcion_c':
            destino_id = request.POST.get('finca_destino_c')
            finca_destino = Finca.objects.filter(pk=destino_id).first() if destino_id else None
            
            # Comprobar si se seleccionó trasladar para algún animal sin haber seleccionado finca destino
            hay_traslado = any(request.POST.get(f'accion_{a.pk}') == 'trasladar' for a in animales_vivos)
            if hay_traslado and not finca_destino:
                messages.error(request, '⚠️ Debes seleccionar la Finca Destino para los animales marcados como "Trasladar".')
            else:
                with transaction.atomic():
                    for animal in animales_vivos:
                        accion = request.POST.get(f'accion_{animal.pk}', 'baja')
                        if accion == 'trasladar' and finca_destino:
                            trasladar_animal(animal, finca_destino, 'PASTO')
                        else:
                            dar_de_baja_animal(animal, f'Baja por eliminación granular de finca {finca.nombre}')
                    finca.delete()
                messages.success(request, f'✅ Finca "{finca.nombre}" eliminada correctamente con resolución granular.')
                return redirect('finca_list')

    return render(request, 'ganaderia/finca_delete_assistant.html', {
        'finca': finca,
        'animales_vivos': animales_vivos,
        'otras_fincas': otras_fincas,
        'active_option': active_option,
    })


@login_required
def incidencias(request):
    """Vista de incidencias y histórico de bajas (Pestañas)."""
    q_baja = request.GET.get('q_baja', '').strip()
    tab = request.GET.get('tab', 'alertas')
    
    # Filtrar exclusivamente sobre hembras vivas con partos en alerta de intervalo activa
    incidencias_activas = Incidencia.objects.filter(
        resuelta=False,
        animal__estado_vital='VIVO',
        parto__alerta_intervalo=True,
        parto__madre__estado_vital='VIVO'
    ).select_related('animal', 'parto').order_by('-created_at')
    
    bajas = Animal.objects.filter(
        estado_vital='BAJA'
    ).select_related('finca', 'madre').order_by('-fecha_baja', '-updated_at')
    
    if q_baja:
        bajas = bajas.filter(crotal__icontains=q_baja)
        tab = 'bajas'
        
    incidencias_count = Parto.objects.filter(
        alerta_intervalo=True,
        madre__estado_vital='VIVO'
    ).count()

    return render(request, 'ganaderia/incidencias.html', {
        'incidencias_activas': incidencias_activas,
        'incidencias_count': incidencias_count,
        'bajas': bajas,
        'q_baja': q_baja,
        'tab': tab,
    })


@login_required
def incidencia_rectificar(request, pk):
    """
    Rectifica la fecha de un parto en conflicto.
    Acepta el ID de una Incidencia o el ID de un Parto.
    Si el intervalo recalculado con los partos adyacentes es >= 270 días:
      - Asigna alerta_intervalo = False y guarda.
      - Marca resuelta la Incidencia vinculada.
    Retorna respuesta compatible con HTMX actualizando el badge #incidencias-counter.
    """
    from django.core.exceptions import ValidationError

    incidencia = Incidencia.objects.filter(pk=pk).first()
    if incidencia and incidencia.parto:
        parto = incidencia.parto
    else:
        parto = get_object_or_404(Parto.objects.select_related('madre'), pk=pk)
        incidencia = Incidencia.objects.filter(parto=parto).first()

    if request.method == 'POST':
        nueva_fecha_str = request.POST.get('nueva_fecha_parto') or request.POST.get('fecha_parto')
        if not nueva_fecha_str:
            messages.error(request, 'Debe indicar una fecha de parto válida.')
            return redirect('incidencias')

        try:
            nueva_fecha = date.fromisoformat(str(nueva_fecha_str).strip())
        except ValueError:
            messages.error(request, f'Formato de fecha inválido: {nueva_fecha_str}')
            return redirect('incidencias')

        observaciones = request.POST.get('observaciones') or parto.observaciones

        try:
            actualizar_parto(parto, nueva_fecha, observaciones)
        except ValidationError as e:
            msg = e.message if hasattr(e, 'message') else str(e)
            messages.error(request, f'Error biológico: {msg}')
            return redirect('incidencias')

        parto.refresh_from_db()
        val = validar_intervalo_parto(parto.madre, parto.fecha_parto, excluir_parto_id=parto.pk)
        if val['alerta'] != 'ROJO':
            parto.alerta_intervalo = False
            parto.save(update_fields=['alerta_intervalo'])
            Incidencia.objects.filter(parto=parto).update(resuelta=True)
            messages.success(request, f'✅ Fecha rectificada ({nueva_fecha.strftime("%d/%m/%Y")}). Intervalo válido (≥ 270d), alerta resuelta.')
        else:
            messages.warning(request, f'⚠️ Fecha actualizada ({nueva_fecha.strftime("%d/%m/%Y")}), pero el intervalo continúa en conflicto: {val["dias_intervalo"]} días.')

        incidencias_count = Parto.objects.filter(
            alerta_intervalo=True,
            madre__estado_vital='VIVO'
        ).count()

        if request.headers.get('HX-Request'):
            badge_display = "none" if incidencias_count == 0 else "inline-block"
            counter_html = (
                f'<span id="incidencias-counter" hx-swap-oob="true" class="badge badge-danger" '
                f'style="font-size: 0.75rem; padding: 0.15rem 0.45rem; border-radius: 9999px; display: {badge_display};">'
                f'{incidencias_count}</span>'
            )
            if not parto.alerta_intervalo:
                return HttpResponse(f'<tr id="incidencia-row-{pk}" style="display: none;"></tr>\n{counter_html}')
            else:
                inc_obj = Incidencia.objects.filter(parto=parto).first()
                desc = inc_obj.descripcion if inc_obj else f"Intervalo: {val['dias_intervalo']} días"
                row_html = (
                    f'<tr id="incidencia-row-{pk}">'
                    f'<td><a href="/animales/{parto.madre.crotal}/"><strong>Crotal {parto.madre.crotal}</strong></a></td>'
                    f'<td><span class="badge badge-danger">Alerta Roja (&lt; 270d)</span></td>'
                    f'<td>{desc}</td>'
                    f'<td><small>{timezone.now().strftime("%d/%m/%Y %H:%M")}</small></td>'
                    f'<td><div class="d-flex gap-1">'
                    f'<button type="button" class="btn btn-sm btn-primary" onclick="abrirModalRectificar(\'{pk}\', \'{parto.pk}\', \'{parto.madre.crotal}\', \'{parto.fecha_parto.strftime("%Y-%m-%d")}\')">✏️ Modificar Fecha</button>'
                    f'<a href="/animales/{parto.madre.crotal}/" class="btn btn-sm btn-outline">👁️ Ver Ficha Madre</a>'
                    f'</div></td></tr>'
                )
                return HttpResponse(f'{row_html}\n{counter_html}')

        return redirect('incidencias')

    return render(request, 'ganaderia/parto_edit.html', {
        'parto': parto,
        'form': PartoEditForm(instance=parto)
    })


@login_required
@require_POST
def incidencias_bajas_purgar(request):
    """
    Elimina permanentemente del sistema todos los registros de animales en estado BAJA en una transacción atómica.
    No afecta a animales activos ni a crías vivas.
    """
    with transaction.atomic():
        total_bajas = Animal.objects.filter(estado_vital='BAJA').count()
        Animal.objects.filter(estado_vital='BAJA').delete()
        Incidencia.objects.filter(animal__isnull=True).delete()

    messages.success(request, f'✅ Historial de bajas vaciado exitosamente ({total_bajas} registros eliminados permanentemente).')

    if request.headers.get('HX-Request'):
        return HttpResponse(status=200, headers={'HX-Refresh': 'true'})

    return redirect(reverse('incidencias') + '?tab=bajas')


@login_required
@require_POST
def incidencias_alertas_limpiar(request):
    """
    Acción administrativa para depurar alertas huérfanas o desactualizadas:
    - Resetea alerta_intervalo=False en partos cuyas madres ya no formen parte del censo activo (BAJA).
    - Marca como resueltas las incidencias de animales en estado BAJA.
    - Evalúa los partos activos con alerta_intervalo=True; si su intervalo adyacente recalculado es >= 270 días, desactiva la alerta y resuelve la incidencia.
    """
    with transaction.atomic():
        Parto.objects.filter(madre__estado_vital='BAJA', alerta_intervalo=True).update(alerta_intervalo=False)
        Incidencia.objects.filter(animal__estado_vital='BAJA').update(resuelta=True)
        Incidencia.objects.filter(parto__madre__estado_vital='BAJA').update(resuelta=True)

        partos_alerta = Parto.objects.filter(alerta_intervalo=True, madre__estado_vital='VIVO').select_related('madre')
        for p in partos_alerta:
            val = validar_intervalo_parto(p.madre, p.fecha_parto, excluir_parto_id=p.pk)
            if val['alerta'] != 'ROJO':
                p.alerta_intervalo = False
                p.save(update_fields=['alerta_intervalo'])
                Incidencia.objects.filter(parto=p).update(resuelta=True)

    messages.success(request, '✅ Depuración de alertas completada. El censo normativo se encuentra actualizado.')

    if request.headers.get('HX-Request'):
        return HttpResponse(status=200, headers={'HX-Refresh': 'true'})

    return redirect('incidencias')


class Echo:
    """Objeto pseudo-buffer que implementa write para devolver el valor en StreamingHttpResponse."""
    def write(self, value):
        return value


@login_required
def exportar_csv(request):
    """Exportación de censo filtrado a archivo CSV Oficial en streaming bajo demanda (cero consumo acumulativo de RAM)."""
    animales = get_filtered_animales(request, with_annotations=True)
    today = date.today()
    
    headers = [
        'Crotal', 'Sexo', 'Raza', 'Fecha Nacimiento', 'Edad (días)',
        'Finca Actual', 'Recinto Actual', 'Días en Cebadero',
        'Crotal Madre', 'Nº de Partos', 'Días desde Último Parto'
    ]

    def generador_csv():
        # Enviar BOM UTF-8 para apertura correcta en Excel
        yield '\ufeff'
        pseudo_buffer = Echo()
        writer = csv.writer(pseudo_buffer, delimiter=';')
        yield writer.writerow(headers)

        for a in animales:
            dias_cebadero = (today - a.fecha_entrada_cebadero).days if a.fecha_entrada_cebadero else '-'
            crotal_madre = a.madre.crotal if a.madre else '-'
            n_partos = getattr(a, 'total_partos', 0) if a.sexo == 'H' else 0
            
            last_date = getattr(a, 'ultimo_parto_fecha', None)
            if a.sexo == 'H' and last_date:
                dias_ultimo_parto = (today - last_date).days
            else:
                dias_ultimo_parto = '-'
                
            finca_nombre = a.finca.nombre if a.finca else '-'
            
            yield writer.writerow([
                a.crotal,
                a.get_sexo_display(),
                a.raza,
                a.fecha_nacimiento.strftime('%d/%m/%Y'),
                a.edad_dias,
                finca_nombre,
                a.get_sub_ubicacion_display(),
                dias_cebadero,
                crotal_madre,
                n_partos,
                dias_ultimo_parto,
            ])

    response = StreamingHttpResponse(generador_csv(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="censo_ganadero_{today.strftime("%Y%m%d")}.csv"'
    return response


@login_required
def exportar_excel(request):
    """Exportación de censo filtrado a libro Excel (.xlsx) (optimizado para evitar N+1 queries)."""
    animales = get_filtered_animales(request, with_annotations=True)
    today = date.today()
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Censo Ganadero"
    
    headers = [
        'Crotal', 'Sexo', 'Raza', 'Fecha Nacimiento', 'Edad (días)',
        'Finca Actual', 'Recinto Actual', 'Días en Cebadero',
        'Crotal Madre', 'Nº de Partos', 'Días desde Último Parto'
    ]
    ws.append(headers)
    
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    for a in animales:
        dias_cebadero = (today - a.fecha_entrada_cebadero).days if a.fecha_entrada_cebadero else '-'
        crotal_madre = a.madre.crotal if a.madre else '-'
        n_partos = getattr(a, 'total_partos', 0) if a.sexo == 'H' else 0
        
        last_date = getattr(a, 'ultimo_parto_fecha', None)
        if a.sexo == 'H' and last_date:
            dias_ultimo_parto = (today - last_date).days
        else:
            dias_ultimo_parto = '-'
            
        finca_nombre = a.finca.nombre if a.finca else '-'
        
        ws.append([
            a.crotal,
            a.get_sexo_display(),
            a.raza,
            a.fecha_nacimiento.strftime('%d/%m/%Y'),
            a.edad_dias,
            finca_nombre,
            a.get_sub_ubicacion_display(),
            dias_cebadero,
            crotal_madre,
            n_partos,
            dias_ultimo_parto,
        ])
        
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="censo_ganadero_{date.today().strftime("%Y%m%d")}.xlsx"'
    wb.save(response)
    return response


@login_required
def api_validar_crotal(request):
    """Endpoint HTMX/AJAX para validar crotal en tiempo real."""
    crotal = request.GET.get('crotal', '')
    resultado = validar_crotal(crotal)
    return JsonResponse(resultado)
