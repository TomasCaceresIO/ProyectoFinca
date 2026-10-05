#!/bin/sh
set -e

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

echo "==> Recolectando archivos estáticos..."
python manage.py collectstatic --noinput

echo "==> Arrancando servidor..."
exec "$@"
