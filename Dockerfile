# Dockerfile para la aplicación Gestión Ganadera MVP (Producción y Docker Compose)
FROM python:3.12-slim

# Variables de entorno
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema operativo y limpiar caché apt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    netcat-openbsd \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar archivos de requerimientos e instalar dependencias Python
COPY requirements.txt requirements-prod.txt /app/
RUN pip install --no-cache-dir -r requirements-prod.txt

# Copiar el resto del código del proyecto
COPY . /app/

# Crear directorios para archivos estáticos y de media
RUN mkdir -p /app/staticfiles /app/media

# Crear usuario de sistema no-root por seguridad
RUN addgroup --system appgroup && adduser --system --group appuser \
    && chown -R appuser:appgroup /app

# Otorgar permisos de ejecución al entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Cambiar a usuario no-root
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
