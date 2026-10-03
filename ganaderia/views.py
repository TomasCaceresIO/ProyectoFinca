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
from django.http import JsonResponse, HttpResponse, Http404
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from .models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia
from .forms import (
    ExplotacionSetupForm, ExplotacionForm, FincaForm, AnimalForm, AnimalBajaForm,
    PartoForm, PartoEditForm, TrasladoForm
)
from .services.animal_services import (
    validar_crotal, validar_intervalo_parto,
    registrar_parto, actualizar_parto, trasladar_animal, dar_de_baja_animal
)
from .services.ai_assistant import procesar_comando_parto


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


@ensure_csrf_cookie
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
        'incidencias': Incidencia.objects.filter(resuelta=False).count(),
    }
    
    finca_id = request.GET.get('finca')
    recinto = request.GET.get('recinto')
    sexo = request.GET.get('sexo')
    raza = request.GET.get('raza')
    orden = request.GET.get('orden', 'crotal')
    
    lista_animales = get_filtered_animales(request)
    fincas = Finca.objects.all()
    incidencias_recientes = Incidencia.objects.filter(resuelta=False).select_related('animal')[:5]
    
    context = {
        'explotacion': explotacion,
        'stats': stats,
        'animales': lista_animales,
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


def animal_list(request):
    """Redirige al Data Grid principal en Home."""
    return redirect('home')


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

    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                animal_actualizado = form.save()
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


def animal_create(request):
    """Crear un nuevo animal."""
    form = AnimalForm(request.POST or None)
    
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            animal = form.save()
            messages.success(request, f'✅ Animal {animal.crotal} registrado correctamente.')
        
        return redirect('animal_detail', crotal=animal.crotal)
    
    return render(request, 'ganaderia/animal_form.html', {'form': form, 'accion': 'Crear'})
    
    return render(request, 'ganaderia/animal_form.html', {'form': form, 'accion': 'Crear'})


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


def incidencias(request):
    """Vista de incidencias y histórico de bajas (Pestañas)."""
    q_baja = request.GET.get('q_baja', '').strip()
    tab = request.GET.get('tab', 'alertas')
    
    incidencias_activas = Incidencia.objects.filter(
        resuelta=False,
        animal__estado_vital='VIVO'
    ).exclude(
        parto__madre__estado_vital='BAJA'
    ).select_related('animal', 'parto').order_by('-created_at')
    
    bajas = Animal.objects.filter(
        estado_vital='BAJA'
    ).select_related('finca', 'madre').order_by('-fecha_baja', '-updated_at')
    
    if q_baja:
        bajas = bajas.filter(crotal__icontains=q_baja)
        tab = 'bajas'
        
    return render(request, 'ganaderia/incidencias.html', {
        'incidencias_activas': incidencias_activas,
        'bajas': bajas,
        'q_baja': q_baja,
        'tab': tab,
    })


def exportar_csv(request):
    """Exportación de censo filtrado a archivo CSV Oficial (optimizado para evitar N+1 queries)."""
    animales = get_filtered_animales(request, with_annotations=True)
    today = date.today()
    
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="censo_ganadero_{today.strftime("%Y%m%d")}.csv"'
    
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    
    headers = [
        'Crotal', 'Sexo', 'Raza', 'Fecha Nacimiento', 'Edad (días)',
        'Finca Actual', 'Recinto Actual', 'Días en Cebadero',
        'Crotal Madre', 'Nº de Partos', 'Días desde Último Parto'
    ]
    writer.writerow(headers)
    
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
        
        writer.writerow([
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
        
    return response


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


def api_validar_crotal(request):
    """Endpoint HTMX/AJAX para validar crotal en tiempo real."""
    crotal = request.GET.get('crotal', '')
    resultado = validar_crotal(crotal)
    return JsonResponse(resultado)


@csrf_exempt
def asistente_preview(request):
    """
    Procesa un comando de lenguaje natural (texto o audio) para registrar un parto.
    Valida biológica y normativamente los datos antes de tocar la base de datos,
    devolviendo la previsualización y alertas detectadas en modal HTML.
    """
    if request.method != 'POST':
        return HttpResponse("Método no permitido", status=405)

    texto = request.POST.get('texto', '').strip()
    audio = request.FILES.get('audio')

    if not texto and not audio:
        return render(request, 'ganaderia/partials/ai_preview_modal.html', {
            'error_general': "Por favor, introduce un texto o graba un audio describiendo el parto."
        })

    texto_o_audio = audio if audio else texto
    audio_type = audio.content_type if audio and hasattr(audio, 'content_type') else 'audio/webm'

    try:
        datos = procesar_comando_parto(texto_o_audio, audio_content_type=audio_type)
    except Exception as e:
        return render(request, 'ganaderia/partials/ai_preview_modal.html', {
            'error_general': f"Error al procesar con el Asistente de IA: {str(e)}"
        })

    if datos.get('error'):
        return render(request, 'ganaderia/partials/ai_preview_modal.html', {
            'error_general': datos['error'],
            'datos': datos,
        })

    crotal_madre = datos.get('crotal_madre')
    fecha_parto_str = datos.get('fecha_parto')
    cria_crotal = datos.get('cria_crotal')
    cria_sexo = datos.get('cria_sexo', 'H')
    cria_raza = datos.get('cria_raza', 'Retinta')
    cria_recinto = datos.get('cria_recinto', 'PASTO')

    errores_bloqueantes = []
    alerta_roja = False
    mensaje_rojo = None
    fecha_ajustada_hoy = False
    fecha_solicitada_original = None
    madre = None
    fecha_parto = None

    # 1. Validar madre en censo activo
    if not crotal_madre:
        errores_bloqueantes.append("No se ha podido identificar el crotal de la madre en el comando.")
    else:
        madre = Animal.objects.filter(crotal=crotal_madre, estado_vital='VIVO', sexo='H').first()
        if not madre:
            animal_existente = Animal.objects.filter(crotal=crotal_madre).first()
            if animal_existente:
                if animal_existente.sexo != 'H':
                    errores_bloqueantes.append(f"El animal con crotal #{crotal_madre} es un Macho y no puede parir.")
                elif animal_existente.estado_vital == 'BAJA':
                    errores_bloqueantes.append(f"La vaca con crotal #{crotal_madre} se encuentra en estado de BAJA.")
                else:
                    errores_bloqueantes.append(f"La vaca con crotal #{crotal_madre} no está disponible en el censo activo.")
            else:
                errores_bloqueantes.append(f"No existe ninguna madre con el crotal #{crotal_madre} en la explotación.")

    # 2. Validar fecha de parto
    if not fecha_parto_str:
        errores_bloqueantes.append("No se pudo determinar la fecha del parto.")
    else:
        try:
            if '-' in fecha_parto_str:
                parts = list(map(int, fecha_parto_str.split('-')[:3]))
                if parts[0] > 1000:
                    fecha_parto = date(parts[0], parts[1], parts[2])
                else:
                    fecha_parto = date(parts[2], parts[1], parts[0])
            elif '/' in fecha_parto_str:
                d, m, y = map(int, fecha_parto_str.split('/')[:3])
                fecha_parto = date(y, m, d)
        except Exception:
            errores_bloqueantes.append(f"Formato de fecha de parto inválido: {fecha_parto_str}")

        if fecha_parto:
            hoy = timezone.now().date()
            if fecha_parto > hoy:
                fecha_solicitada_original = fecha_parto
                fecha_parto = hoy
                fecha_ajustada_hoy = True

            if madre:
                if fecha_parto < madre.fecha_nacimiento:
                    errores_bloqueantes.append(
                        f"La fecha del parto ({fecha_parto.strftime('%d/%m/%Y')}) no puede ser anterior al nacimiento de la madre ({madre.fecha_nacimiento.strftime('%d/%m/%Y')})."
                    )
                elif (fecha_parto - madre.fecha_nacimiento).days < 540:
                    dias_vida = (fecha_parto - madre.fecha_nacimiento).days
                    errores_bloqueantes.append(
                        f"Incongruencia biológica: La madre tiene menos de 18 meses al parir ({dias_vida} días, mínimo legal: 540 días)."
                    )

    # 3. Validar crotal de la cría
    if not cria_crotal:
        errores_bloqueantes.append("No se ha podido identificar el crotal de la cría en el comando.")
    elif len(cria_crotal) != 4 or not cria_crotal.isdigit():
        errores_bloqueantes.append(f"El crotal de la cría ('{cria_crotal}') debe contener exactamente 4 dígitos numéricos.")
    else:
        val_crotal = validar_crotal(cria_crotal)
        if val_crotal['bloqueante']:
            errores_bloqueantes.append(val_crotal['mensaje'])

    # 4. Validar intervalo reproductivo (RN-04: Mínimo 270 días)
    if madre and fecha_parto and not errores_bloqueantes:
        val_intervalo = validar_intervalo_parto(madre, fecha_parto)
        if not val_intervalo['valido']:
            alerta_roja = True
            dias_intervalo = val_intervalo.get('dias_intervalo', '?')
            mensaje_rojo = (
                f"⚠️ CONFLICTO NORMATIVO (Diputación): El intervalo con el parto anterior es de {dias_intervalo} días "
                f"(< 9 meses / 270 días). El registro provocará una Alerta Roja oficial."
            )

    return render(request, 'ganaderia/partials/ai_preview_modal.html', {
        'datos': datos,
        'madre': madre,
        'crotal_madre': crotal_madre,
        'fecha_parto': fecha_parto,
        'fecha_parto_str': fecha_parto.strftime('%Y-%m-%d') if fecha_parto else fecha_parto_str,
        'fecha_ajustada_hoy': fecha_ajustada_hoy,
        'fecha_solicitada_original': fecha_solicitada_original,
        'cria_crotal': cria_crotal,
        'cria_sexo': cria_sexo,
        'cria_raza': cria_raza,
        'cria_recinto': cria_recinto,
        'errores_bloqueantes': errores_bloqueantes,
        'alerta_roja': alerta_roja,
        'mensaje_rojo': mensaje_rojo,
        'puede_confirmar': len(errores_bloqueantes) == 0,
    })


@csrf_exempt
def asistente_ejecutar(request):
    """
    Ejecuta atómicamente el registro del parto y la cría confirmados desde el asistente de IA.
    """
    if request.method != 'POST':
        return HttpResponse("Método no permitido", status=405)

    crotal_madre = request.POST.get('crotal_madre')
    fecha_parto_str = request.POST.get('fecha_parto')
    cria_crotal = request.POST.get('cria_crotal')
    cria_sexo = request.POST.get('cria_sexo', 'H')
    cria_raza = request.POST.get('cria_raza', 'Retinta')
    cria_recinto = request.POST.get('cria_recinto', 'PASTO')
    forzar = request.POST.get('forzar') in ['1', 'true', 'True', True]

    madre = Animal.objects.filter(crotal=crotal_madre, estado_vital='VIVO', sexo='H').first()
    if not madre:
        messages.error(request, f"La madre #{crotal_madre} no se encuentra en el censo activo.")
        return redirect('home')

    try:
        if '-' in fecha_parto_str:
            y, m, d = map(int, fecha_parto_str.split('-')[:3])
            fecha_parto = date(y, m, d)
        else:
            d, m, y = map(int, fecha_parto_str.split('/')[:3])
            fecha_parto = date(y, m, d)
    except Exception:
        messages.error(request, f"Fecha de parto no válida: {fecha_parto_str}")
        return redirect('home')

    crias = [{
        'crotal': cria_crotal,
        'sexo': cria_sexo,
        'raza': cria_raza,
    }]

    try:
        with transaction.atomic():
            resultado = registrar_parto(
                madre=madre,
                fecha_parto=fecha_parto,
                forzar=forzar,
                observaciones="Registrado mediante Asistente de IA (Voz/Texto)",
                crias=crias,
            )
            cria = resultado['cria']
            if cria_recinto and cria.sub_ubicacion != cria_recinto:
                cria.sub_ubicacion = cria_recinto
                cria.save()

            if resultado['alerta'] == 'ROJO':
                messages.warning(
                    request,
                    f"⚠️ Parto de la vaca {madre.crotal} registrado con Alerta Roja de intervalo (< 270 días). "
                    f"Cría {cria.crotal} añadida al censo."
                )
            else:
                messages.success(
                    request,
                    f"✅ Parto de la vaca {madre.crotal} y cría {cria.crotal} ({cria.get_sexo_display()}, {cria.raza}) registrados con éxito."
                )

        if request.headers.get('HX-Request'):
            response = HttpResponse(status=200)
            response['HX-Redirect'] = reverse('animal_detail', kwargs={'crotal': cria.crotal})
            return response
        return redirect('animal_detail', crotal=cria.crotal)

    except Exception as e:
        messages.error(request, f"Error al ejecutar el registro de parto: {str(e)}")
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=200)
            response['HX-Redirect'] = reverse('home')
            return response
        return redirect('home')

