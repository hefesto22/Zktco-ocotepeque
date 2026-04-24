"""Vista de Configuración maestra (Sub-2.4).

Contiene un ``CTkTabview`` con dos pestañas — Departamentos y Cargos —
cada una montada con un ``CatalogoTab`` parametrizado con los callables
del ``ConfiguracionController``.

El guard de permiso ``MANAGE_SETTINGS`` vive en el controller (cada
método ``list_*/create_*/rename_*/archive_*/unarchive_*`` está decorado).
"""

from __future__ import annotations

import customtkinter as ctk

from ui.components.catalogo_tab import CatalogoTab
from ui.controllers.configuracion_controller import ConfiguracionController


class ConfiguracionView(ctk.CTkFrame):
    """Vista raíz del módulo Configuración."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: ConfiguracionController,
    ) -> None:
        """Construye la vista.

        Args:
            master: Contenedor Tk donde se monta.
            controller: Controller ya inyectado con session + servicio.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller

        self._construir_ui()

    def _construir_ui(self) -> None:
        """Arma título + tabview con las dos pestañas."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Configuración",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))

        tabview = ctk.CTkTabview(self)
        tabview.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        tab_deps = tabview.add("Departamentos")
        tab_cargos = tabview.add("Cargos")

        CatalogoTab(
            tab_deps,
            title_singular="Departamento",
            list_fn=self._controller.list_departamentos,
            create_fn=self._controller.create_departamento,
            rename_fn=self._controller.rename_departamento,
            archive_fn=self._controller.archive_departamento,
            unarchive_fn=self._controller.unarchive_departamento,
        ).pack(expand=True, fill="both")

        CatalogoTab(
            tab_cargos,
            title_singular="Cargo",
            list_fn=self._controller.list_cargos,
            create_fn=self._controller.create_cargo,
            rename_fn=self._controller.rename_cargo,
            archive_fn=self._controller.archive_cargo,
            unarchive_fn=self._controller.unarchive_cargo,
        ).pack(expand=True, fill="both")
