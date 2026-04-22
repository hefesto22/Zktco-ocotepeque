"""Vista principal (MainWindow).

Layout:
    ┌─────────────────────────────────────────────────┐
    │  Sidebar  │            Contenido                │
    │  (botones │    (swap de PlaceholderView por     │
    │   por per-│     módulo; Phase 2+ traerá vistas  │
    │   miso)   │     reales)                         │
    │           │                                     │
    ├───────────┴─────────────────────────────────────┤
    │  Status bar: usuario · rol · estado ZKTeco      │
    └─────────────────────────────────────────────────┘

Doble defensa de permisos:
    1. El sidebar SOLO pinta los botones de módulos cuyo permiso la
       sesión posee (filtro vía ``permission_guard.filter_visible``).
    2. Aún así, el ``MainController.open_*`` está decorado con
       ``@require_permission`` — si un bug futuro dejara un botón visible,
       el click lanzaría ``PermissionDeniedError`` y NO montaría la vista.

Estado ZKTeco en la status bar:
    - En Phase 1 mostramos "Sin conexión" fijo. En Phase 4 (módulo
      ZKTeco) se conectará a un observable/poller real.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

import customtkinter as ctk

from core.services.errors import PermissionDeniedError
from core.services.session import Session
from ui.controllers.main_controller import MainController
from ui.guards.permission_guard import filter_visible
from ui.menu_items import MENU_ITEMS, MenuItem
from ui.views.placeholder_view import PlaceholderView


class MainFrame(ctk.CTkFrame):
    """Frame principal de la app post-login."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        session: Session,
        controller: MainController,
        on_logout: Callable[[], None],
    ) -> None:
        """Construye el frame.

        Args:
            master: Ventana raíz donde se monta.
            session: Sesión activa — determina qué botones del sidebar
                se renderizan y qué se muestra en la status bar.
            controller: Controller ya inyectado (``open_view`` apunta a
                este frame vía callback).
            on_logout: Callback invocado cuando el usuario cierra sesión.
        """
        super().__init__(master, corner_radius=0)
        self._session = session
        self._controller = controller
        self._on_logout = on_logout
        self._log = logging.getLogger(self.__class__.__name__)

        # Index por code para mostrar el título/descripción en el placeholder.
        self._items_by_code: Dict[str, MenuItem] = {item.code: item for item in MENU_ITEMS}

        # Widget actual del área central (se destruye al cambiar de módulo).
        self._current_view: Optional[ctk.CTkBaseClass] = None

        self._construir_ui()
        self._mostrar_bienvenida()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma sidebar, área de contenido y status bar con grid."""
        # Grid raíz: columna 0 fija (sidebar), columna 1 elástica (contenido).
        # Fila 0 elástica (cuerpo), fila 1 fija (status bar).
        self.grid_columnconfigure(0, weight=0, minsize=220)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self._sidebar = self._construir_sidebar()
        self._sidebar.grid(row=0, column=0, sticky="nsew")

        self._content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)

        self._status_bar = self._construir_status_bar()
        self._status_bar.grid(row=1, column=0, columnspan=2, sticky="sew")

    def _construir_sidebar(self) -> ctk.CTkFrame:
        """Arma el sidebar con los botones filtrados por permiso."""
        sidebar = ctk.CTkFrame(self, corner_radius=0, width=220)

        # Header del sidebar: nombre de la app.
        header = ctk.CTkLabel(
            sidebar,
            text="ZKTeco Attendance",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        header.pack(padx=16, pady=(20, 4), fill="x")

        subheader = ctk.CTkLabel(
            sidebar,
            text="Grupo Olympo",
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "gray70"),
            anchor="w",
        )
        subheader.pack(padx=16, pady=(0, 20), fill="x")

        # Botones de módulos visibles para la sesión activa.
        visibles = filter_visible(MENU_ITEMS, self._session)
        for item in visibles:
            btn = ctk.CTkButton(
                sidebar,
                text=item.label,
                anchor="w",
                height=36,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray25"),
                command=lambda code=item.code: self._abrir(code),
            )
            btn.pack(padx=8, pady=2, fill="x")

        # Spacer para empujar el botón de logout al fondo.
        spacer = ctk.CTkFrame(sidebar, fg_color="transparent")
        spacer.pack(expand=True, fill="both")

        btn_logout = ctk.CTkButton(
            sidebar,
            text="Cerrar sesión",
            height=36,
            command=self._on_btn_logout,
        )
        btn_logout.pack(padx=8, pady=(8, 16), fill="x")

        return sidebar

    def _construir_status_bar(self) -> ctk.CTkFrame:
        """Barra inferior con usuario, rol y estado ZKTeco."""
        bar = ctk.CTkFrame(self, corner_radius=0, height=28)

        texto = (
            f"Usuario: {self._session.username}"
            f"   ·   Rol: {self._session.role_code}"
            f"   ·   ZKTeco: sin conexión"
        )
        self._status_label = ctk.CTkLabel(
            bar,
            text=texto,
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "gray70"),
            anchor="w",
        )
        self._status_label.pack(padx=12, pady=4, fill="x")
        return bar

    # ── Handlers ──────────────────────────────────────────────────────────

    def _abrir(self, code: str) -> None:
        """Click en un botón del sidebar.

        Delega al controller (que valida el permiso con el decorador);
        el controller invoca el callback ``open_view`` que montamos aquí.
        Si el permiso falla, capturamos y mostramos un aviso en el área
        central — sin cerrar la app.
        """
        try:
            self._controller.open_by_code(code)
        except PermissionDeniedError:
            # Defensa secundaria: un botón visible sin permiso indicaría un
            # bug de filter_visible. Lo reportamos pero no tumbamos la app.
            self._log.error("Click en módulo sin permiso: %s", code)
            self._mostrar_acceso_denegado(code)

    def _on_btn_logout(self) -> None:
        """Click en el botón de cerrar sesión."""
        self._log.info("Logout: usuario=%s", self._session.username)
        self._on_logout()

    # ── Swap de vistas en el área central ─────────────────────────────────

    def _montar_view(self, nueva: ctk.CTkBaseClass) -> None:
        """Destruye la vista anterior y monta la nueva en el contenedor."""
        if self._current_view is not None:
            self._current_view.destroy()
        nueva.grid(row=0, column=0, sticky="nsew")
        self._current_view = nueva

    def _mostrar_bienvenida(self) -> None:
        """Monta una pantalla de bienvenida al entrar al MainWindow."""
        bienvenida = PlaceholderView(
            self._content,
            title=f"Bienvenido, {self._session.username}",
            description=(
                "Seleccione un módulo del menú lateral para comenzar. "
                "Solo se muestran los módulos disponibles para su rol "
                f"({self._session.role_code})."
            ),
        )
        self._montar_view(bienvenida)

    def _mostrar_placeholder_por_code(self, code: str) -> None:
        """Monta ``PlaceholderView`` con los textos del ``MenuItem``."""
        item = self._items_by_code[code]
        placeholder = PlaceholderView(
            self._content,
            title=item.label,
            description=item.description,
        )
        self._montar_view(placeholder)

    def _mostrar_acceso_denegado(self, code: str) -> None:
        """Monta un placeholder de acceso denegado (fallback defensivo)."""
        aviso = PlaceholderView(
            self._content,
            title="Acceso denegado",
            description=(
                f"Su rol actual ({self._session.role_code}) no tiene permiso "
                f"para abrir este módulo ({code}). Si considera que es un "
                "error, contacte al SUPERADMIN."
            ),
        )
        self._montar_view(aviso)

    # ── Callback público para el controller ───────────────────────────────

    def open_view(self, code: str) -> None:
        """Callback que el controller invoca tras pasar el decorador de permisos.

        Se expone como método público para que el composition root pueda
        pasárselo al ``MainController`` al construirlo.
        """
        self._mostrar_placeholder_por_code(code)
