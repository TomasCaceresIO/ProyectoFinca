from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Health Check (Público para UptimeRobot)
    path('healthcheck/', views.healthcheck, name='healthcheck'),
    
    # Autenticación
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    # Setup Wizard
    path('setup/', views.setup_wizard, name='setup_wizard'),
    
    # Explotacion
    path('explotacion/editar/', views.explotacion_edit, name='explotacion_edit'),
    
    # Home
    path('', views.home, name='home'),
    
    # Animales
    path('animales/', views.animal_list, name='animal_list'),
    path('animales/nuevo/', views.animal_create, name='animal_create'),
    path('animales/<str:crotal>/', views.animal_detail, name='animal_detail'),
    path('animales/<str:crotal>/editar/', views.animal_edit, name='animal_edit'),
    path('animales/<str:crotal>/baja/', views.animal_baja, name='animal_baja'),
    path('animales/<str:crotal>/traslado/', views.animal_traslado, name='animal_traslado'),
    
    # Partos
    path('partos/nuevo/', views.parto_create, name='parto_create'),
    path('partos/<int:pk>/editar/', views.parto_edit, name='parto_edit'),
    
    # Fincas
    path('fincas/', views.finca_list, name='finca_list'),
    path('fincas/nueva/', views.finca_create, name='finca_create'),
    path('fincas/<int:pk>/eliminar/', views.finca_delete, name='finca_delete'),
    
    # Incidencias / Bajas
    path('incidencias/', views.incidencias, name='incidencias'),
    path('incidencias/<int:pk>/rectificar/', views.incidencia_rectificar, name='incidencia_rectificar'),
    path('incidencias/bajas/purgar/', views.incidencias_bajas_purgar, name='incidencias_bajas_purgar'),
    path('incidencias/alertas/limpiar/', views.incidencias_alertas_limpiar, name='incidencias_alertas_limpiar'),
    
    # Exportaciones (CSV / Excel)
    path('exportar/csv/', views.exportar_csv, name='exportar_csv'),
    path('exportar/excel/', views.exportar_excel, name='exportar_excel'),
    
    # API endpoints
    path('api/validar-crotal/', views.api_validar_crotal, name='api_validar_crotal'),
]
