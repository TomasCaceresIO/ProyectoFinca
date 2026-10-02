# MVP Ganadero - Gestión Ganadera (Django + HTMX + Tailwind)

Sistema de gestión ganadera desarrollado con Django 5, HTMX y Tailwind CSS para el control de censo activo, reproductoras, partos, incidencias y movimientos de ganado.

## 🚀 Requisitos y Configuración

- **Python:** 3.12+
- **Django:** 5.0+
- **Base de Datos:** SQLite por defecto (`db.sqlite3`) o PostgreSQL en producción mediante `DATABASE_URL`.

---

## 🛠️ Instalación y Arranque Rápido (Local Python)

1. **Crear y activar el entorno virtual:**
   ```bash
   python -m venv venv
   # En Windows PowerShell:
   .\venv\Scripts\Activate.ps1
   ```

2. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Ejecutar migraciones de la base de datos:**
   ```bash
   python manage.py makemigrations ganaderia
   python manage.py migrate
   ```

4. **Cargar datos de prueba (Seed Data):**
   ```bash
   python manage.py seed_demo_data
   ```

5. **Iniciar el servidor de desarrollo:**
   ```bash
   python manage.py runserver
   ```
   Accede a la aplicación en: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 🐳 Despliegue con Docker y PostgreSQL

Para levantar la infraestructura completa encapsulada en contenedores con base de datos PostgreSQL 16:

1. **Levantar todos los servicios con Docker Compose:**
   ```bash
   docker compose up --build
   ```

2. **Cargar datos de demostración en el contenedor:**
   ```bash
   docker compose exec web python manage.py seed_demo_data
   ```

3. **Crear un superusuario de administración:**
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

4. **Ejecutar la suite completa de tests dentro del contenedor:**
   ```bash
   docker compose exec web pytest
   ```

---

## 🧪 Batería de Tests y Calidad de Código (QA)

Ejecutar la suite completa de tests de integración y unidad:

```bash
# Con Django test runner:
python manage.py test ganaderia.tests

# Con pytest:
pytest
```

---

## 📐 Funcionalidades Principales

1. **Onboarding Wizard (`/setup/`):** Configuración inicial de Explotación (Código REGA), Finca inicial y recintos (Pasto, Cebadero, Apartado). Redirección automática si no existe explotación.
2. **Panel Principal & Censo Activo (`/`):** Data Grid dinámico con filtro multifactorial (Finca, Recinto, Sexo, Raza, Ordenación) con respuestas parciales HTMX (`<tbody>`).
3. **Ficha Detalle del Animal (`/animales/<str:crotal>/`):** Visualización de datos, partos con badge de intervalo biológico (verde >= 270d, rojo < 270d), enlace defensivo a la cría censada y acciones inmediatas (Traslado, Baja, Registrar Parto, Modificar Fecha).
4. **Validación de Crotales:** Formato estricto de 4 dígitos numéricos, restricción de unicidad para animales VIVOS y aviso de Alerta Amarilla para crotales históricos reutilizados.
5. **Validación Bidireccional de Partos (270 días):** Detección de colisión con partos anteriores y posteriores más cercanos.
6. **Gestión de Traslados (Cebadero):** Sellado automático de `fecha_entrada_cebadero` y reset a `None` al retornar a Pasto/Apartado.
