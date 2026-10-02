"""
Vistas de la aplicación Gestión Ganadera.
"""
import csv
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from datetime import date

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from django.utils import timezone

from .models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia
from .forms import (
    ExplotacionSetupForm, ExplotacionForm, FincaForm, AnimalForm, AnimalBajaForm,
    PartoForm, PartoEditForm, TrasladoForm
)
from .services.animal_services import (
    validar_crotal, validar_intervalo_parto,
    registrar_parto, actualizar_parto, trasladar_animal, dar_de_baja_animal
)


def get_filtered_animales(request):
    """Helper que aplica los mismos filtros multifactor que la tabla principal."""
    animales = Animal.objects.filter(estado_vital='VIVO').select_related(
        'finca', 'madre'
    ).prefetch_related('partos_como_madre')
    
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
        lista.sort(
            key=lambda a: (a.dias_desde_ultimo_parto if a.dias_desde_ultimo_parto is not None else -1),
            reverse=True
        )
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
    """Ficha detallada de un animal."""
    animal = get_object_or_404(
        Animal.objects.select_related('finca', 'madre'),
        crotal=crotal
    )
    partos = animal.partos_como_madre.select_related('cria', 'cria2').order_by('-fecha_parto')
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


def animal_baja(request, crotal):
    """Dar de baja un animal."""
    animal = get_object_or_404(Animal, crotal=crotal, estado_vital='VIVO')
    form = AnimalBajaForm(request.POST or None)
    
    if request.method == 'POST' and form.is_valid():
        motivo = form.cleaned_data.get('motivo', '')
        dar_de_baja_animal(animal, motivo)
        messages.success(request, f'Animal {animal.crotal} dado de baja correctamente.')
        return redirect('home')
    
    return render(request, 'ganaderia/animal_baja.html', {'animal': animal, 'form': form})


def animal_traslado(request, crotal):
    """Trasladar un animal a otra finca/sub_ubicación."""
    animal = get_object_or_404(Animal, crotal=crotal, estado_vital='VIVO')
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
        madre = Animal.objects.filter(crotal=madre_crotal, sexo='H').first()
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
        })

    if request.method == 'POST':
        opcion = request.POST.get('opcion')
        
        if opcion == 'opcion_a':
            destino_id = request.POST.get('finca_destino')
            if not destino_id:
                messages.error(request, 'Debes seleccionar una finca de destino.')
            else:
                finca_destino = get_object_or_404(Finca, pk=destino_id)
                with transaction.atomic():
                    for animal in animales_vivos:
                        trasladar_animal(animal, finca_destino, 'PASTO')
                    finca.delete()
                messages.success(request, f'✅ Finca "{finca.nombre}" eliminada. Todos sus animales fueron trasladados a {finca_destino.nombre}.')
                return redirect('finca_list')

        elif opcion == 'opcion_b':
            motivo = request.POST.get('motivo_baja', 'Vaciado por eliminación de finca')
            with transaction.atomic():
                for animal in animales_vivos:
                    dar_de_baja_animal(animal, motivo)
                finca.delete()
            messages.success(request, f'✅ Finca "{finca.nombre}" eliminada. Todos sus animales fueron dados de baja.')
            return redirect('finca_list')

        elif opcion == 'opcion_c':
            destino_id = request.POST.get('finca_destino_c')
            finca_destino = Finca.objects.filter(pk=destino_id).first() if destino_id else None
            with transaction.atomic():
                for animal in animales_vivos:
                    accion = request.POST.get(f'accion_{animal.pk}', 'baja')
                    if accion == 'trasladar' and finca_destino:
                        trasladar_animal(animal, finca_destino, 'PASTO')
                    else:
                        dar_de_baja_animal(animal, f'Baja granular por eliminación de {finca.nombre}')
                finca.delete()
            messages.success(request, f'✅ Finca "{finca.nombre}" eliminada con resolución granular.')
            return redirect('finca_list')

    return render(request, 'ganaderia/finca_delete_assistant.html', {
        'finca': finca,
        'animales_vivos': animales_vivos,
        'otras_fincas': otras_fincas,
    })


def incidencias(request):
    """Vista de incidencias y histórico de bajas (Pestañas)."""
    q_baja = request.GET.get('q_baja', '').strip()
    tab = request.GET.get('tab', 'alertas')
    
    incidencias_activas = Incidencia.objects.filter(
        resuelta=False
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
    """Exportación de censo filtrado a archivo CSV Oficial."""
    animales = get_filtered_animales(request)
    
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="censo_ganadero_{date.today().strftime("%Y%m%d")}.csv"'
    
    # Escribir BOM UTF-8 para compatibilidad en Excel en español
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    
    headers = [
        'Crotal', 'Sexo', 'Raza', 'Fecha Nacimiento', 'Edad (días)',
        'Finca Actual', 'Recinto Actual', 'Días en Cebadero',
        'Crotal Madre', 'Nº de Partos', 'Días desde Último Parto'
    ]
    writer.writerow(headers)
    
    for a in animales:
        dias_cebadero = (date.today() - a.fecha_entrada_cebadero).days if a.fecha_entrada_cebadero else '-'
        crotal_madre = a.madre.crotal if a.madre else '-'
        n_partos = a.partos_como_madre.count() if a.sexo == 'H' else 0
        dias_ultimo_parto = a.dias_desde_ultimo_parto if (a.sexo == 'H' and a.dias_desde_ultimo_parto is not None) else '-'
        
        writer.writerow([
            a.crotal,
            a.get_sexo_display(),
            a.raza,
            a.fecha_nacimiento.strftime('%Y-%m-%d'),
            a.edad_dias,
            a.finca.nombre,
            a.get_sub_ubicacion_display(),
            dias_cebadero,
            crotal_madre,
            n_partos,
            dias_ultimo_parto,
        ])
        
    return response


def exportar_excel(request):
    """Exportación de censo filtrado a libro Excel (.xlsx)."""
    animales = get_filtered_animales(request)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Censo Ganadero"
    
    headers = [
        'Crotal', 'Sexo', 'Raza', 'Fecha Nacimiento', 'Edad (días)',
        'Finca Actual', 'Recinto Actual', 'Días en Cebadero',
        'Crotal Madre', 'Nº de Partos', 'Días desde Último Parto'
    ]
    ws.append(headers)
    
    # Estilo de cabecera
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    for a in animales:
        dias_cebadero = (date.today() - a.fecha_entrada_cebadero).days if a.fecha_entrada_cebadero else '-'
        crotal_madre = a.madre.crotal if a.madre else '-'
        n_partos = a.partos_como_madre.count() if a.sexo == 'H' else 0
        dias_ultimo_parto = a.dias_desde_ultimo_parto if (a.sexo == 'H' and a.dias_desde_ultimo_parto is not None) else '-'
        
        ws.append([
            a.crotal,
            a.get_sexo_display(),
            a.raza,
            a.fecha_nacimiento.strftime('%Y-%m-%d'),
            a.edad_dias,
            a.finca.nombre,
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
