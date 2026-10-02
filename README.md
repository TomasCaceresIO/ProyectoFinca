# 🐄 Gestión Ganadera MVP

Sistema de gestión para explotaciones ganaderas. Desarrollado con Django 5 + HTMX.

---

## 📋 Requisitos previos

- **Python 3.12+** ([descargar](https://www.python.org/downloads/))
- **Git** ([descargar](https://git-scm.com/))

---

## 🚀 Arranque rápido (primera vez)

### 1. Clonar / Acceder al proyecto

```bash
# Si ya tienes el directorio:
cd ProyectoFinca
```

### 2. Crear y activar el entorno virtual

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

> ✅ Verás el prefijo `(venv)` en tu terminal cuando esté activo.

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

El archivo `.env` ya está creado con configuración para desarrollo local (SQLite).  
Si quieres revisar o modificar los valores, edita `.env`:

```env
DEBUG=True
SECRET_KEY=tu-clave-secreta
DATABASE_URL=sqlite:///db.sqlite3   # SQLite local
# DATABASE_URL=postgres://user:pass@host:5432/db  # PostgreSQL (producción)
```

### 5. Ejecutar migraciones

```bash
python manage.py migrate
```

### 6. Cargar datos de prueba (demo)

```bash
python manage.py seed_demo_data
```

Esto cargará automáticamente:
- 🏢 **1 Explotación**: Ganadería Las Encinas (ES-190420001234)
- 🌿 **2 Fincas**: Las Encinas + El Robledal
- 📍 **5 Ubicaciones**: PASTO, CEBADERO, APARTADO
- 🐄 **15 animales** de prueba (incluyendo alertas de demostración)
- ⚠️ **Alerta Roja RN-04**: Crotal 1002 (intervalo < 270 días)
- ⚠️ **Alerta Amarilla RN-03**: Crotal 9999 en BAJA (prueba reutilización)

> Para resetear y recargar: `python manage.py seed_demo_data --reset`

### 7. Arrancar el servidor de desarrollo

```bash
python manage.py runserver
```

Abre tu navegador en: **http://127.0.0.1:8000**

---

## 🗂️ Estructura del Proyecto

```
ProyectoFinca/
├── config/                     # Configuración del proyecto Django
│   ├── __init__.py
│   ├── settings.py             # Settings con django-environ
│   ├── urls.py                 # URLs raíz
│   ├── wsgi.py
│   └── asgi.py
├── ganaderia/                  # App principal de dominio
│   ├── models.py               # Modelos: Explotacion, Finca, Ubicacion, Animal, Parto, Incidencia
│   ├── views.py                # Vistas de todas las secciones
│   ├── urls.py                 # URLs de la app
│   ├── forms.py                # Formularios
│   ├── admin.py                # Panel de administración
│   ├── middleware.py           # Middleware de onboarding
│   ├── apps.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── animal_services.py  # Lógica de negocio aislada
│   ├── management/
│   │   └── commands/
│   │       └── seed_demo_data.py  # Comando de semillas
│   └── templatetags/
│       └── __init__.py
├── templates/                  # Templates globales
│   ├── base.html               # Layout base
│   └── ganaderia/              # Templates de la app
│       ├── home.html
│       ├── setup_wizard.html
│       ├── animal_list.html
│       ├── animal_detail.html
│       ├── animal_form.html
│       ├── animal_baja.html
│       ├── animal_traslado.html
│       ├── parto_form.html
│       ├── finca_list.html
│       └── incidencias.html
├── static/
│   ├── css/main.css            # Estilos globales
│   └── js/main.js              # Scripts globales
├── .env                        # Variables de entorno (NO subir a Git)
├── .env.example                # Plantilla de variables de entorno
├── .gitignore
├── requirements.txt
├── pytest.ini
└── README.md
```

---

## 🏛️ Arquitectura

### Reglas de Negocio implementadas

| Código | Descripción | Comportamiento |
|--------|-------------|----------------|
| **RN-01** | Crotal: exactamente 4 dígitos numéricos | Bloqueante. Error de formato. |
| **RN-02** | Unicidad de crotal activo | Bloqueante. Solo 1 animal VIVO por crotal. |
| **RN-03** | Reutilización de crotal histórico (en BAJA) | ⚠️ Alerta Amarilla. Requiere confirmación del usuario. |
| **RN-04** | Intervalo entre partos < 270 días | 🔴 Alerta Roja. Usuario puede forzar el guardado. |
| **RN-05** | Reproductora trasladada al cebadero | ⚠️ Advertencia no bloqueante. Requiere confirmación. |

### Diagrama de modelos

```
Explotacion (1) ──── (N) Finca (1) ──── (N) Ubicacion
                                               │
                              Animal (N) ──────┘
                              │  └── madre (FK → self)
                              │
                         (N) Parto ──── cria (FK → Animal)
                              │
                         (N) Incidencia
```

---

## 🔄 Flujo de primer inicio

1. Al arrancar por primera vez, el **Onboarding Middleware** detecta que no existe ninguna `Explotacion`.
2. Redirige automáticamente a `/setup/` (Wizard de configuración).
3. El usuario introduce el nombre y código REGA de su explotación.
4. A partir de ahí, todas las funcionalidades están disponibles.

---

## 🛠️ Comandos útiles de desarrollo

```bash
# Ejecutar tests
pytest

# Comprobar estilo de código
flake8 ganaderia/

# Crear nuevo superusuario para el admin
python manage.py createsuperuser

# Acceder al admin de Django
# http://127.0.0.1:8000/admin/

# Ver migraciones pendientes
python manage.py showmigrations

# Crear nuevas migraciones tras cambiar modelos
python manage.py makemigrations
python manage.py migrate
```

---

## 🌐 Despliegue en producción (guía rápida)

El proyecto está preparado para cambiar de SQLite a PostgreSQL **sin refactorizar nada**, solo cambiando `.env`:

```env
DEBUG=False
SECRET_KEY=clave-secreta-muy-larga-y-aleatoria
ALLOWED_HOSTS=tu-dominio.com,www.tu-dominio.com
DATABASE_URL=postgres://usuario:contraseña@host:5432/nombre_db
```

Pasos adicionales para producción:
1. `pip install gunicorn`
2. `python manage.py collectstatic`
3. Configurar servidor web (nginx + gunicorn) o plataforma como Railway, Render, Fly.io

---

## 📝 Notas de desarrollo

- **Sin papelera**: Las bajas de animales son directas (`estado_vital = 'BAJA'`), no hay periodo de espera de 7 días.
- **HTMX**: Incluido para validaciones asíncronas (ej. validación de crotal en tiempo real).
- **Base de datos**: SQLite en local, PostgreSQL en producción, configurado via `DATABASE_URL`.
- **Estáticos**: HTMX se carga desde CDN en desarrollo. En producción, considera instalarlo localmente.
