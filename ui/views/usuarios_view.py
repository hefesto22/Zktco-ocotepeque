"""Vista del módulo "Usuarios y roles" (Sub-2.7a).

Estructura preparada para Sub-2.7b: ``CTkTabview`` con un solo tab
("Usuarios") en esta entrega; el tab "Roles" se agregará después sin
romper el wiring actual.

El guard de permiso ``MANAGE_USERS`` ya vive en el controller
(decorador en cada método). El `MenuItem` "users" del sidebar requiere
ese mismo permiso, así que solo SUPERADMIN o ADMIN llegan acá.
"""

from __future__ import annotations

import customtkinter as ctk

from ui.components.usuarios_tab import UsuariosTab
from ui.controllers.usuarios_controller import UsuariosController


class UsuariosView(ctk.CTkFrame):
    """Vista raíz del módulo Usuarios y roles."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: UsuariosController,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._construir_ui()

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Usuarios y roles",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))

        tabview = ctk.CTkTabview(self)
        tabview.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        tab_usuarios = tabview.add("Usuarios")
        # Tab "Roles" se agrega en Sub-2.7b — la vista ya está lista
        # para extenderse sin tocar este wiring.

        UsuariosTab(
            tab_usuarios,
            list_usuarios_fn=self._controller.list_usuarios,
            list_roles_fn=self._controller.list_roles_asignables,
            create_fn=self._controller.create_usuario,
            update_fn=self._controller.update_usuario,
            reset_password_fn=self._controller.reset_password,
            deactivate_fn=self._controller.deactivate_usuario,
            reactivate_fn=self._controller.reactivate_usuario,
            unlock_fn=self._controller.unlock_usuario,
        ).pack(expand=True, fill="both")
