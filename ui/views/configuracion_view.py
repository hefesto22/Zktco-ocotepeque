"""Vista de Configuración maestra (Sub-2.4 + Sub-2.4b).

Contiene un ``CTkTabview`` con tres pestañas — Departamentos, Cargos y
Dispositivos — cada una montada con su componente parametrizado:

    - Departamentos / Cargos → ``CatalogoTab`` genérico (Sub-2.4).
    - Dispositivos           → ``DispositivosTab`` dedicado (Sub-2.4b),
      por tener 3 campos editables (nombre, ip, puerto) que no encajan
      en el componente del catálogo simple.

El guard de permiso ``MANAGE_SETTINGS`` vive en el controller (cada
método ``list_*/create_*/rename_*/update_*/archive_*/unarchive_*`` está
decorado).
"""

from __future__ import annotations

import customtkinter as ctk

from ui.components.catalogo_tab import CatalogoTab
from ui.components.dispositivos_tab import DispositivosTab
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
            controller: Controller ya inyectado con session + servicios.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller

        self._construir_ui()

    def _construir_ui(self) -> None:
        """Arma título + tabview con las tres pestañas."""
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
        tab_disp = tabview.add("Dispositivos")

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

        DispositivosTab(
            tab_disp,
            list_fn=self._controller.list_dispositivos,
            create_fn=self._controller.create_dispositivo,
            update_fn=self._controller.update_dispositivo,
            archive_fn=self._controller.archive_dispositivo,
            unarchive_fn=self._controller.unarchive_dispositivo,
        ).pack(expand=True, fill="both")
