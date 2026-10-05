#!/bin/sh
set -e

# Asegurar propiedad y permisos de directorios de estáticos y media
# Si se ejecuta como root (o si los volúmenes montados se inicializan como root),
# corregir propiedad para appuser.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/staticfiles /app/media
    chown -R appuser:appuser /app/staticfiles /app/media
    chmod -R 775 /app/staticfiles /app/media
fi

echo "==> Comprobando disponibilidad de la base de datos..."
python << 'EOF'
import sys
import time
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import connection
from django.db.utils import OperationalError

max_retries = 30
while max_retries > 0:
    try:
        connection.ensure_connection()
        print("==> Base de datos conectada exitosamente.")
        sys.exit(0)
    except OperationalError as err:
        max_retries -= 1
        print(f"==> Esperando conexión a la BD ({max_retries} intentos restantes): {err}")
        time.sleep(2)

print("==> ERROR: Tiempo de espera agotado para conectar a la base de datos.")
sys.exit(1)
EOF

echo "==> Aplicando migraciones..."
python manage.py migrate --noinput

echo "==> Inicializando datos y credenciales..."
python init_admin.py

echo "==> Recolectando archivos estáticos..."
python manage.py collectstatic --noinput --clear

echo "==> Arrancando servidor..."
exec "$@"
