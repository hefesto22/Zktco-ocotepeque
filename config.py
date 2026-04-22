"""Configuración global de la aplicación.

Contiene constantes del sistema: identidad de la app, parámetros de seguridad,
límites de sesión y rutas de archivos. Valores sensibles NUNCA se guardan aquí
(para eso se usará la tabla de configuración en BD, cifrada si aplica).
"""

from pathlib import Path

# ── Identidad de la aplicación ────────────────────────────────────────────────
APP_NAME: str = "ZKTeco Attendance Desktop App"
APP_VENDOR: str = "Grupo Olympo"
APP_VERSION: str = "0.1.0"  # pre-alpha, en desarrollo

# ── Rutas ─────────────────────────────────────────────────────────────────────
# BASE_DIR apunta a la raíz del proyecto (donde está este archivo).
BASE_DIR: Path = Path(__file__).resolve().parent

# Archivo SQLite local. En prod queda junto al .exe en %APPDATA%\Grupo Olympo\...,
# pero durante desarrollo vive en la raíz del proyecto.
DATABASE_FILENAME: str = "zkteco_app.db"
DATABASE_PATH: Path = BASE_DIR / DATABASE_FILENAME

# Carpeta de migraciones SQL.
MIGRATIONS_DIR: Path = BASE_DIR / "infrastructure" / "database" / "migrations"

# ── Seguridad ─────────────────────────────────────────────────────────────────
# bcrypt cost factor. Mínimo 12 según las reglas del proyecto.
BCRYPT_COST_FACTOR: int = 12

# Bloqueo de cuenta tras N intentos fallidos consecutivos.
MAX_FAILED_LOGIN_ATTEMPTS: int = 5

# Duración del bloqueo temporal tras MAX_FAILED_LOGIN_ATTEMPTS (en minutos).
# Política: lockout temporal fijo (Opción B aprobada). Tras este periodo, el
# siguiente login con password correcta resetea el contador y permite entrar.
LOCKOUT_DURATION_MINUTES: int = 15

# Expiración de sesión por inactividad (en minutos). 0 = nunca.
SESSION_TIMEOUT_MINUTES: int = 30

# ── Política de contraseñas ───────────────────────────────────────────────────
# Se aplica al crear el primer SUPERADMIN (Setup Wizard) y, más adelante, a
# cualquier otro flujo de creación o cambio de password.
MIN_PASSWORD_LENGTH: int = 8
PASSWORD_REQUIRE_DIGIT: bool = True
PASSWORD_REQUIRE_LETTER: bool = True

# ── Logging ───────────────────────────────────────────────────────────────────
# Nivel por defecto. En prod se baja a INFO o WARNING vía config en BD.
LOG_LEVEL: str = "DEBUG"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
