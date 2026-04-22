"""Composition root de la UI.

Este módulo es el único lugar donde se instancian los servicios,
controllers y vistas. El resto del árbol recibe sus dependencias por
inyección en constructor — cumpliendo la REGLA SOLID-D del proyecto.

Flujo (router):
    1. Abre la BD y corre migraciones pendientes.
    2. Construye los servicios una sola vez (auth, permisos, setup, etc.).
    3. Crea la ``CTk`` raíz.
    4. Si ``SetupWizardService.is_first_run()`` → monta ``SetupWizardFrame``.
       Tras crear el SUPERADMIN, cae al login.
    5. Si no es first-run → monta ``LoginFrame``.
    6. Tras login OK → monta ``MainFrame`` con la sesión.
    7. En logout → descarta la sesión y vuelve al login.

Threading:
    - La verificación bcrypt del login es I/O-bound ligera (~250ms).
      Se despacha al hilo daemon via ``run_async_ui`` inyectado en el
      ``LoginController``. La UI queda siempre responsiva.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable, Optional

import customtkinter as ctk

import config
from core.models.usuario import Usuario
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.rol_repository_sqlite import RolRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.auth_service import AuthService
from core.services.password_policy import PasswordPolicy
from core.services.permission_service import PermissionService
from core.services.session import Session
from core.services.setup_wizard_service import SetupWizardService
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner
from infrastructure.security.bcrypt_hasher import BcryptHasher
from ui.async_util import run_async_ui
from ui.controllers.login_controller import LoginController
from ui.controllers.main_controller import MainController
from ui.controllers.setup_controller import SetupController
from ui.views.login_window import LoginFrame
from ui.views.main_window import MainFrame
from ui.views.setup_wizard_window import SetupWizardFrame


class _Services:
    """Contenedor de servicios construidos una sola vez en el arranque.

    No es un "container" de DI; solo agrupa las instancias para que el
    router las pueda pasar cuando las necesite. Mantener esto como clase
    simple (no dict) permite que mypy --strict tipe cada campo.
    """

    def __init__(
        self,
        auth: AuthService,
        permission: PermissionService,
        setup: SetupWizardService,
    ) -> None:
        self.auth = auth
        self.permission = permission
        self.setup = setup


def run() -> int:
    """Arranca la UI. Devuelve el exit code (0 = cierre normal).

    Returns:
        0 si la app cerró normalmente, 2 si falló la inicialización de BD.
    """
    log = logging.getLogger("ui.app")

    database = Database(config.DATABASE_PATH)
    if not _run_migrations(database, log):
        return 2

    services = _build_services(database)

    root = _crear_root()
    router = _Router(root, services, log)
    router.start()

    log.info("Entrando al mainloop de customtkinter.")
    root.mainloop()
    log.info("Mainloop finalizado. Saliendo.")
    return 0


# ── Helpers de arranque ──────────────────────────────────────────────────────


def _run_migrations(database: Database, log: logging.Logger) -> bool:
    """Corre migraciones pendientes. Devuelve False ante error."""
    try:
        runner = MigrationsRunner(database, config.MIGRATIONS_DIR)
        applied = runner.run()
    except (OSError, sqlite3.Error):
        log.exception("No se pudo inicializar la base de datos.")
        return False
    if applied:
        log.info("Migraciones aplicadas: %s", ", ".join(applied))
    return True


def _build_services(database: Database) -> _Services:
    """Instancia servicios con sus repos/hashers/loggers reales."""
    usuario_repo = UsuarioRepositorySQLite(database)
    rol_repo = RolRepositorySQLite(database)
    audit_repo = AuditLogRepositorySQLite(database)

    hasher = BcryptHasher(config.BCRYPT_COST_FACTOR)
    audit_logger = AuditLogger(audit_repo)

    password_policy = PasswordPolicy(
        min_length=config.MIN_PASSWORD_LENGTH,
        require_digit=config.PASSWORD_REQUIRE_DIGIT,
        require_letter=config.PASSWORD_REQUIRE_LETTER,
    )

    auth_service = AuthService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=rol_repo,
        hasher=hasher,
        audit_logger=audit_logger,
        max_failed_attempts=config.MAX_FAILED_LOGIN_ATTEMPTS,
        lockout_duration_minutes=config.LOCKOUT_DURATION_MINUTES,
    )

    setup_service = SetupWizardService(
        usuario_read=usuario_repo,
        usuario_write=usuario_repo,
        rol_read=rol_repo,
        hasher=hasher,
        audit_logger=audit_logger,
        password_policy=password_policy,
    )

    return _Services(
        auth=auth_service,
        permission=PermissionService(),
        setup=setup_service,
    )


def _crear_root() -> ctk.CTk:
    """Crea la ventana raíz con tema y tamaño por defecto."""
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title(f"{config.APP_NAME} — {config.APP_VENDOR}")
    root.geometry("1000x620")
    root.minsize(880, 560)
    return root


# ── Router: transiciones entre wizard → login → main ────────────────────────


class _Router:
    """Gestiona las transiciones entre los tres frames principales.

    Mantiene referencia al frame actualmente montado para destruirlo
    antes de montar el siguiente. No guarda ``Session`` persistente: la
    sesión vive únicamente en el ``MainFrame`` activo.
    """

    def __init__(self, root: ctk.CTk, services: _Services, log: logging.Logger) -> None:
        self._root = root
        self._services = services
        self._log = log
        self._current_frame: Optional[ctk.CTkFrame] = None

    def start(self) -> None:
        """Decide qué frame mostrar al arrancar la app."""
        if self._services.setup.is_first_run():
            self._log.info("First run detectado — mostrando Setup Wizard.")
            self._mostrar_wizard()
        else:
            self._log.info("First run ya completado — mostrando Login.")
            self._mostrar_login()

    # ── Transiciones ──────────────────────────────────────────────────────

    def _swap(self, nuevo: ctk.CTkFrame) -> None:
        """Destruye el frame actual y monta el nuevo ocupando toda la raíz."""
        if self._current_frame is not None:
            self._current_frame.destroy()
        nuevo.pack(expand=True, fill="both")
        self._current_frame = nuevo

    def _mostrar_wizard(self) -> None:
        setup_controller = SetupController(self._services.setup)
        frame = SetupWizardFrame(
            self._root,
            controller=setup_controller,
            on_success=self._on_wizard_ok,
        )
        self._swap(frame)

    def _mostrar_login(self) -> None:
        # El login_controller recibe un async_runner placeholder; la view
        # lo sustituye por uno ligado a su propio widget antes de disparar
        # el trabajo — así mantenemos al controller agnóstico de Tk.
        placeholder_runner: Callable[..., Any] = lambda work, ok, err: None  # noqa: E731
        login_controller = LoginController(self._services.auth, placeholder_runner)
        frame = LoginFrame(
            self._root,
            controller=login_controller,
            on_success=self._on_login_ok,
        )
        self._swap(frame)

    def _mostrar_main(self, session: Session) -> None:
        # Creamos el MainFrame primero porque el controller necesita su
        # método ``open_view`` como callback. Inyectamos el controller
        # después llamando a un setter; mantenemos la inyección por
        # constructor cableando con un closure pre-hecho.
        main_frame_holder: dict[str, MainFrame] = {}

        def open_view_callback(code: str) -> None:
            main_frame_holder["frame"].open_view(code)

        controller = MainController(
            session=session,
            permission_service=self._services.permission,
            open_view=open_view_callback,
        )
        frame = MainFrame(
            self._root,
            session=session,
            controller=controller,
            on_logout=self._on_logout,
        )
        main_frame_holder["frame"] = frame

        # Reemplazamos el async_runner del login: ya no aplica. Tras login,
        # el futuro despacho asíncrono lo gestionará cada controller con
        # el widget actual.
        self._swap(frame)

    # ── Callbacks de cada frame ───────────────────────────────────────────

    def _on_wizard_ok(self, _usuario: Usuario) -> None:
        """Tras crear el SUPERADMIN, vamos al login."""
        self._mostrar_login()

    def _on_login_ok(self, session: Session) -> None:
        """Tras login válido, montamos la ventana principal."""
        # Aquí reemplazamos el async_runner que usa el login: el flujo
        # pasó — de aquí en adelante la sesión vive en el MainFrame.
        self._mostrar_main(session)

    def _on_logout(self) -> None:
        """El usuario cerró sesión — volvemos al login."""
        self._mostrar_login()


# Re-exportamos run_async_ui aquí simbólicamente, para dejar explícito en
# el composition root que existe — aunque hoy solo lo use LoginFrame.
__all__ = ["run", "run_async_ui"]
