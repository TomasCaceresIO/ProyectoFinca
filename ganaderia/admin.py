from django.contrib import admin
from .models import Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia


@admin.register(Explotacion)
class ExplotacionAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'codigo_rega', 'created_at']
    search_fields = ['nombre', 'codigo_rega']


@admin.register(Finca)
class FincaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'explotacion']
    list_filter = ['explotacion']
    search_fields = ['nombre']


@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'finca', 'tipo', 'n_animales']
    list_filter = ['tipo', 'finca']


@admin.register(Animal)
class AnimalAdmin(admin.ModelAdmin):
    list_display = ['crotal', 'sexo', 'raza', 'estado_vital', 'finca_actual', 'sub_ubicacion', 'fecha_nacimiento']
    list_filter = ['sexo', 'estado_vital', 'raza', 'finca_actual']
    search_fields = ['crotal']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        ('Identificación', {'fields': ['crotal', 'sexo', 'raza', 'fecha_nacimiento', 'madre']}),
        ('Estado', {'fields': ['estado_vital', 'fecha_baja', 'motivo_baja']}),
        ('Ubicación', {'fields': ['finca_actual', 'sub_ubicacion', 'fecha_entrada_cebadero']}),
        ('Auditoría', {'fields': ['created_at', 'updated_at']}),
    ]


@admin.register(Parto)
class PartoAdmin(admin.ModelAdmin):
    list_display = ['madre', 'fecha_parto', 'cria', 'alerta_intervalo']
    list_filter = ['alerta_intervalo']
    search_fields = ['madre__crotal']


@admin.register(Incidencia)
class IncidenciaAdmin(admin.ModelAdmin):
    list_display = ['animal', 'tipo', 'descripcion', 'resuelta', 'created_at']
    list_filter = ['tipo', 'resuelta']
    search_fields = ['animal__crotal']
