"""Punto de entrada de la ZKTeco Attendance Desktop App.

Fase 0 (actual): setup de logging, verificación de dependencias críticas
e inicialización de la base de datos aplicando migraciones pendientes.

Fase 1 (siguiente): auth + UI. Cuando esté lista, main() levantará la
ventana de login después de _initialize_database().
"""

from __future__ import annotations

import logging
import sqlite3
import sys

import config
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner


def _setup_logging() -> None:
    """Configura el logging raíz según config.py."""
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format=config.LOG_FORMAT,
        stream=sys.stdout,
    )


def _check_critical_imports(log: logging.Logger) -> bool:
    """Valida que las 5 dependencias de producción importen correctamente."""
    try:
        import bcrypt  # noqa: F401
        import customtkinter  # noqa: F401
        import openpyxl  # noqa: F401
        import zk  # noqa: F401  (pyzk expone el módulo `zk`)
    except ImportError as exc:
        log.error("Falta una dependencia crítica: %s", exc)
        return False
    log.info("Dependencias críticas OK (bcrypt, customtkinter, openpyxl, pyzk).")
    return True


def _initialize_database(log: logging.Logger) -> bool:
    """Abre la BD y aplica migraciones pendientes."""
    log.info("Inicializando base de datos: %s", config.DATABASE_PATH)
    db = Database(config.DATABASE_PATH)
    runner = MigrationsRunner(db, config.MIGRATIONS_DIR)
    try:
        applied = runner.run()
    except (OSError, sqlite3.Error):
        log.exception("No se pudo inicializar la base de datos.")
        return False
    if applied:
        log.info("Migraciones nuevas aplicadas: %s", ", ".join(applied))
    else:
        log.info("Base de datos ya estaba al día.")
    return True


def main() -> int:
    """Entry point. Retorna el exit code del proceso."""
    _setup_logging()
    log = logging.getLogger("main")
    log.info("Iniciando %s v%s (%s)", config.APP_NAME, config.APP_VERSION, config.APP_VENDOR)
    log.info("BASE_DIR: %s", config.BASE_DIR)

    if not _check_critical_imports(log):
        return 1

    if not _initialize_database(log):
        return 2

    log.info("Fase 0 lista. Siguiente: autenticación y roles (Fase 1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
