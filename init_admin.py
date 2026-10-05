import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import call_command
from ganaderia.models import Explotacion

User = get_user_model()

# 1. Cargar semillas si la base de datos no tiene datos previos
if not Explotacion.objects.exists():
    print("==> Base de datos vacia. Ejecutando seed_demo_data...")
    try:
        call_command("seed_demo_data")
        print("==> Datos demo cargados exitosamente.")
    except Exception as e:
        print(f"==> Error cargando demo data: {e}")
else:
    print("==> Explotaciones existentes detectadas. Omitiendo seed_demo_data.")

# 2. Crear superusuario inicial desde variables de entorno
username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@finca.com")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

if username and password:
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username=username, email=email, password=password)
        print(f"==> Superusuario '{username}' creado exitosamente.")
    else:
        print(f"==> El usuario '{username}' ya existe.")
else:
    print("==> DJANGO_SUPERUSER_USERNAME o DJANGO_SUPERUSER_PASSWORD no definidos. Omitiendo creacion de superuser.")
