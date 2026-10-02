#!/bin/sh
set -e

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
python manage.py collectstatic --noinput

exec "$@"
