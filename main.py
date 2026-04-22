"""Punto de entrada de la ZKTeco Attendance Desktop App.

Secuencia de arranque:
    1. Configurar logging según ``config.py``.
    2. Verificar que las dependencias críticas importan bien.
    3. Delegar a ``ui.app.run()`` — allí vive el composition root:
       abre BD + aplica migraciones + construye servicios + levanta UI.

El smoke-check de BD que existía en Fase 0 se movió a ``ui.app.run()``
para evitar abrir la conexión dos veces en el arranque real. Se puede
seguir corriendo ``python -m bin.setup_wizard`` para preparar el
sistema antes de abrir la UI.
"""

from __future__ import annotations

import logging
import sys

import config


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


def main() -> int:
    """Entry point. Retorna el exit code del proceso."""
    _setup_logging()
    log = logging.getLogger("main")
    log.info("Iniciando %s v%s (%s)", config.APP_NAME, config.APP_VERSION, config.APP_VENDOR)
    log.info("BASE_DIR: %s", config.BASE_DIR)

    if not _check_critical_imports(log):
        return 1

    # Importamos ui.app aquí (no arriba) para que, si faltan dependencias
    # de UI, el error salga en ``_check_critical_imports`` — no como un
    # ImportError opaco al arrancar este módulo.
    from ui.app import run as run_ui

    return run_ui()


if __name__ == "__main__":
    sys.exit(main())
