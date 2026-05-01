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
    - Default al arrancar: "Sin conexión" (no se conoce historial vivo).
    - Tras un sync OK: ``SincronizacionView`` invoca un callback que
      llega hasta ``refresh_status_zkteco(...)`` y actualiza el texto
      a "última sync HH:MM" (Sub-2.4c / fix cosmético post-Plan B).
    - Phase 4 podrá reemplazar el callback por un poller en tiempo
      real sin tocar este método.
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
from ui.views.welcome_view import ResumenSistema, WelcomeView

# Factory de vista: recibe el contenedor padre y devuelve el widget a montar.
# El composition root (``ui/app.py``) construye estas factories con los
# controllers ya cableados — MainFrame solo las invoca por ``code``.
ViewFactory = Callable[[ctk.CTkBaseClass], ctk.CTkBaseClass]


class MainFrame(ctk.CTkFrame):
    """Frame principal de la app post-login."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        session: Session,
        controller: MainController,
        on_logout: Callable[[], None],
        view_factories: Optional[Dict[str, ViewFactory]] = None,
        initial_zkteco_status: str = "sin conexión",
        resumen_sistema: Optional[ResumenSistema] = None,
    ) -> None:
        """Construye el frame.

        Args:
            master: Ventana raíz donde se monta.
            session: Sesión activa — determina qué botones del sidebar
                se renderizan y qué se muestra en la status bar.
            controller: Controller ya inyectado (``open_view`` apunta a
                este frame vía callback).
            on_logout: Callback invocado cuando el usuario cierra sesión.
            view_factories: Mapa opcional ``{code: factory}``. Si el
                ``code`` abierto está en el mapa, se monta la vista real;
                si no, cae al ``PlaceholderView`` genérico (módulo aún
                sin implementación).
            initial_zkteco_status: Texto inicial del segmento ZKTeco de
                la status bar. El composition root lo construye leyendo
                la última sincronización OK de BD para que el estado
                persista entre logouts/logins (Sub-2.7c).
        """
        super().__init__(master, corner_radius=0)
        self._session = session
        self._controller = controller
        self._on_logout = on_logout
        self._view_factories: Dict[str, ViewFactory] = view_factories or {}
        self._initial_zkteco_status = initial_zkteco_status
        self._resumen_sistema = resumen_sistema
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
            text="BioMuni",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        header.pack(padx=16, pady=(20, 4), fill="x")

        subheader = ctk.CTkLabel(
            sidebar,
            text="Municipalidad de Ocotepeque",
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

        # Estado ZKTeco se guarda separado para poder refrescarlo a
        # demanda sin reconstruir el resto del label. El valor inicial
        # lo arma el composition root leyendo la última sync OK de BD
        # (Sub-2.7c) — así el estado persiste tras logout/login.
        self._zkteco_status = self._initial_zkteco_status
        self._status_label = ctk.CTkLabel(
            bar,
            text=self._build_status_text(),
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "gray70"),
            anchor="w",
        )
        self._status_label.pack(padx=12, pady=4, fill="x")
        return bar

    def _build_status_text(self) -> str:
        """Compone el texto completo del status bar.

        Centraliza el formato para que ``refresh_status_zkteco`` no
        duplique el orden de los campos. El separador "·" rodeado de
        espacios mantiene legibilidad incluso con fuentes condensadas.
        """
        return (
            f"Usuario: {self._session.username}"
            f"   ·   Rol: {self._session.role_code}"
            f"   ·   ZKTeco: {self._zkteco_status}"
        )

    # ── API pública para refrescar el estado ZKTeco ───────────────────────

    def refresh_status_zkteco(self, nuevo_estado: str) -> None:
        """Actualiza el segmento "ZKTeco: ..." de la status bar.

        Lo invoca el composition root tras un sync exitoso (vía un
        callback inyectado en la ``SincronizacionView``). Es idempotente:
        llamarlo dos veces seguidas con el mismo texto no causa flicker.

        Args:
            nuevo_estado: Texto que reemplaza el estado actual. Se
                espera un string corto, en español, sin punto final
                (ej. ``"última sync 14:32"`` o ``"sin conexión"``).
        """
        if nuevo_estado == self._zkteco_status:
            return
        self._zkteco_status = nuevo_estado
        self._status_label.configure(text=self._build_status_text())

    # ── Handlers ──────────────────────────────────────────────────────────

    def _abrir(self, code: str) -> None:
        """Click en un botón del sidebar.

        Delega al controller (que valida el permiso con el decorador);
        el controller invoca el callback ``open_view`` que montamos aquí.
        Si el permiso falla, capturamos y mostramos un aviso en el área
        central — sin cerrar la app.

        Cualquier otra excepción al construir la vista también se captura
        para evitar que la app quede en estado inconsistente: en un .exe
        ``--windowed`` (sin consola) un raise no manejado se pierde
        silenciosamente y el usuario solo ve que el click "no hace nada".
        Loggeamos con traceback completo y mostramos un placeholder.
        """
        try:
            self._controller.open_by_code(code)
        except PermissionDeniedError:
            # Defensa secundaria: un botón visible sin permiso indicaría un
            # bug de filter_visible. Lo reportamos pero no tumbamos la app.
            self._log.error("Click en módulo sin permiso: %s", code)
            self._mostrar_acceso_denegado(code)
        except Exception:
            # Boundary handler de UI: la regla "nunca capturar Exception
            # genérica" se relaja aquí porque sin este catch, los errores
            # del bundle PyInstaller (hidden imports faltantes, data files
            # no incluidos, etc.) se pierden y la app queda muda.
            self._log.exception("Error inesperado al abrir módulo '%s'", code)
            self._mostrar_error_inesperado(code)

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
        """Monta la pantalla de bienvenida del MainFrame.

        Sub-3.4: si el composition root pasó un ``ResumenSistema``,
        se monta el ``WelcomeView`` rico (saludo + stats + tip). Si no
        — ej. tests viejos que no inyectan resumen — cae al
        ``PlaceholderView`` legacy para preservar retrocompatibilidad.
        """
        if self._resumen_sistema is not None:
            bienvenida: ctk.CTkBaseClass = WelcomeView(
                self._content,
                username=self._session.username,
                role_code=self._session.role_code,
                resumen=self._resumen_sistema,
            )
        else:
            bienvenida = PlaceholderView(
                self._content,
                title=f"Bienvenido, {self._session.username}",
                description=(
                    "Seleccione un módulo del menú lateral para comenzar. "
                    "Solo se muestran los módulos disponibles para su rol "
                    f"({self._session.role_code})."
                ),
                mostrar_aviso=False,
            )
        self._montar_view(bienvenida)

    def _mostrar_vista_por_code(self, code: str) -> None:
        """Monta la vista real si hay factory; si no, ``PlaceholderView``.

        El lookup es por ``code`` del ``MenuItem`` (ej. ``"settings"``).
        La factory recibe el contenedor padre y devuelve el widget ya
        construido con todas sus dependencias inyectadas.
        """
        factory = self._view_factories.get(code)
        if factory is not None:
            self._montar_view(factory(self._content))
            return
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

    def _mostrar_error_inesperado(self, code: str) -> None:
        """Placeholder cuando una excepción no esperada rompe la apertura.

        Mensaje en español al usuario sin stack trace; el traceback ya
        quedó loggeado por ``_abrir`` con ``logger.exception``.
        """
        item = self._items_by_code.get(code)
        titulo_modulo = item.label if item is not None else code
        aviso = PlaceholderView(
            self._content,
            title="Error inesperado",
            description=(
                f"No se pudo abrir el módulo «{titulo_modulo}». "
                "Revise el archivo de log para más detalles "
                "o contacte al administrador."
            ),
        )
        self._montar_view(aviso)

    # ── Callback público para el controller ───────────────────────────────

    def open_view(self, code: str) -> None:
        """Callback que el controller invoca tras pasar el decorador de permisos.

        Se expone como método público para que el composition root pueda
        pasárselo al ``MainController`` al construirlo.
        """
        self._mostrar_vista_por_code(code)
