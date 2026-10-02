from django.urls import path
from . import views

urlpatterns = [
    # Setup Wizard
    path('setup/', views.setup_wizard, name='setup_wizard'),
    
    # Home
    path('', views.home, name='home'),
    
    # Animales
    path('animales/', views.animal_list, name='animal_list'),
    path('animales/nuevo/', views.animal_create, name='animal_create'),
    path('animales/<str:crotal>/', views.animal_detail, name='animal_detail'),
    path('animales/<str:crotal>/baja/', views.animal_baja, name='animal_baja'),
    path('animales/<str:crotal>/traslado/', views.animal_traslado, name='animal_traslado'),
    
    # Partos
    path('partos/nuevo/', views.parto_create, name='parto_create'),
    path('partos/<int:pk>/editar/', views.parto_edit, name='parto_edit'),
    
    # Fincas
    path('fincas/', views.finca_list, name='finca_list'),
    
    # Incidencias
    path('incidencias/', views.incidencias, name='incidencias'),
    path('incidencias/<int:pk>/resolver/', views.incidencia_resolver, name='incidencia_resolver'),
    
    # API endpoints
    path('api/validar-crotal/', views.api_validar_crotal, name='api_validar_crotal'),
]
