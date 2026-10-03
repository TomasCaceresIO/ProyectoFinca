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

if [ "$DATABASE_ENGINE" = "django.db.backends.postgresql" ] || [ -n "$DATABASE_URL" ]; then
    echo "Esperando a que la base de datos PostgreSQL esté lista..."
    while ! nc -z ${POSTGRES_HOST:-db} ${POSTGRES_PORT:-5432}; do
      sleep 0.5
    done
    echo "Base de datos PostgreSQL disponible."
fi

# Aplicar migraciones
echo "Aplicando migraciones..."
python manage.py migrate --noinput

# Recolectar archivos estáticos para producción
echo "Recolectando estáticos..."
python manage.py collectstatic --noinput --clear

exec "$@"
