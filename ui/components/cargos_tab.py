"""``CargosTab`` — vista del tab Cargos con soporte de departamento (Sub-3.2.A).

Variante específica de ``CatalogoTab`` para cargos. A diferencia del
componente genérico:

    - El formulario de "+ Agregar cargo" pide nombre **+ departamento
      opcional** (dropdown).
    - Cada fila muestra ``Nombre  ·  Depto: <nombre>`` o
      ``Nombre  ·  Global``.
    - El botón "Editar" permite cambiar el departamento de un cargo
      existente.

Diseño visual:

    ┌─ Cargos ─────────────────────────────────────────────────────────┐
    │  [✓ Ver archivados]                                  [Refrescar] │
    ├──────────────────────────────────────────────────────────────────┤
    │  Tesorero  ·  Depto: Tesorería     [Editar] [Archivar]            │
    │  Auxiliar  ·  Global               [Editar] [Archivar]            │
    │  ...                                                              │
    ├──────────────────────────────────────────────────────────────────┤
    │  Nuevo cargo:  [______________]   Aplica a:  [combo ▾]  [Agregar]│
    └──────────────────────────────────────────────────────────────────┘

Permisos: el guard ``MANAGE_SETTINGS`` vive en el controller — esta
vista solo invoca callables que ya validan permisos.
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Callable, List, Optional

import customtkinter as ctk

from core.models.cargo import Cargo
from core.models.departamento import Departamento

# Sentinel para el dropdown "Sin departamento (global)".
_GLOBAL_LABEL = "(Global — todos los departamentos)"


class CargosTab(ctk.CTkFrame):
    """Tab Cargos con soporte de departamento opcional."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        list_cargos_fn: Callable[..., List[Cargo]],
        list_departamentos_fn: Callable[..., List[Departamento]],
        create_fn: Callable[..., Cargo],
        rename_fn: Callable[[int, str], None],
        set_departamento_fn: Callable[[int, Optional[int]], None],
        archive_fn: Callable[[int], None],
        unarchive_fn: Callable[[int], None],
    ) -> None:
        """Construye el tab.

        Args:
            master: Contenedor Tk donde se monta.
            list_cargos_fn: ``controller.list_cargos`` (acepta
                ``solo_activos`` por kwarg).
            list_departamentos_fn: ``controller.list_departamentos``
                (igual contrato).
            create_fn: ``controller.create_cargo(nombre, departamento_id)``.
            rename_fn: ``controller.rename_cargo(cargo_id, nuevo_nombre)``.
            set_departamento_fn:
                ``controller.set_cargo_departamento(cargo_id, departamento_id)``.
            archive_fn: ``controller.archive_cargo(cargo_id)``.
            unarchive_fn: ``controller.unarchive_cargo(cargo_id)``.
        """
        super().__init__(master, fg_color="transparent")
        self._list_cargos = list_cargos_fn
        self._list_departamentos = list_departamentos_fn
        self._create_fn = create_fn
        self._rename_fn = rename_fn
        self._set_departamento_fn = set_departamento_fn
        self._archive_fn = archive_fn
        self._unarchive_fn = unarchive_fn
        self._log = logging.getLogger(self.__class__.__name__)

        self._ver_archivados_var = ctk.BooleanVar(value=False)
        self._nuevo_nombre_var = ctk.StringVar(value="")
        self._nuevo_depto_label_var = ctk.StringVar(value=_GLOBAL_LABEL)

        # Mapas label → id para los dropdowns.
        self._depto_label_to_id: dict[str, Optional[int]] = {_GLOBAL_LABEL: None}
        self._depto_id_to_label: dict[Optional[int], str] = {None: _GLOBAL_LABEL}

        self._construir_ui()
        self._refrescar()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        chk = ctk.CTkCheckBox(
            toolbar,
            text="Ver archivados",
            variable=self._ver_archivados_var,
            command=self._refrescar,
        )
        chk.pack(side="left", padx=(0, 8))
        ctk.CTkButton(toolbar, text="Refrescar", width=90, command=self._refrescar).pack(
            side="right"
        )

        self._lista = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._lista.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self._lista.grid_columnconfigure(0, weight=1)

        # Fila inferior: nuevo cargo + departamento + Agregar.
        nuevo_frame = ctk.CTkFrame(self, fg_color="transparent")
        nuevo_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        nuevo_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(nuevo_frame, text="Nuevo cargo:").grid(
            row=0, column=0, padx=(0, 6), sticky="w"
        )
        ctk.CTkEntry(
            nuevo_frame,
            textvariable=self._nuevo_nombre_var,
            placeholder_text="Escriba el nombre",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(nuevo_frame, text="Aplica a:").grid(row=0, column=2, padx=(0, 6), sticky="w")
        self._combo_depto_nuevo = ctk.CTkOptionMenu(
            nuevo_frame,
            values=[_GLOBAL_LABEL],
            variable=self._nuevo_depto_label_var,
            width=220,
        )
        self._combo_depto_nuevo.grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(nuevo_frame, text="Agregar", width=110, command=self._on_agregar).grid(
            row=0, column=4
        )

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refrescar(self) -> None:
        """Recarga cargos + departamentos y re-pinta la lista."""
        try:
            ver_arch = self._ver_archivados_var.get()
            cargos = self._list_cargos(solo_activos=not ver_arch)
            deps = self._list_departamentos(solo_activos=True)
        except Exception:  # noqa: BLE001
            self._log.exception("Error refrescando CargosTab")
            messagebox.showerror("Error", "No se pudieron cargar los datos.")
            return

        # Refrescar mapas y dropdown de departamento.
        self._depto_label_to_id = {_GLOBAL_LABEL: None}
        self._depto_id_to_label = {None: _GLOBAL_LABEL}
        labels = [_GLOBAL_LABEL]
        for d in deps:
            if d.id is None:
                continue
            self._depto_label_to_id[d.nombre] = d.id
            self._depto_id_to_label[d.id] = d.nombre
            labels.append(d.nombre)
        self._combo_depto_nuevo.configure(values=labels)
        if self._nuevo_depto_label_var.get() not in labels:
            self._nuevo_depto_label_var.set(_GLOBAL_LABEL)

        # Repintar la lista.
        for child in self._lista.winfo_children():
            child.destroy()

        if not cargos:
            ctk.CTkLabel(self._lista, text="No hay cargos cargados todavía.", anchor="w").grid(
                row=0, column=0, sticky="ew", padx=8, pady=8
            )
            return

        for fila_idx, cargo in enumerate(cargos):
            self._render_fila(cargo, fila_idx)

    def _render_fila(self, cargo: Cargo, fila_idx: int) -> None:
        depto_label = self._depto_id_to_label.get(
            cargo.departamento_id,
            f"(depto desconocido id={cargo.departamento_id})",
        )
        if cargo.departamento_id is None:
            depto_label = "Global"

        archivado = " — ARCHIVADO" if not cargo.is_active else ""
        texto = f"{cargo.nombre}  ·  {depto_label}{archivado}"
        fila = ctk.CTkFrame(self._lista, fg_color="transparent")
        fila.grid(row=fila_idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(fila, text=texto, anchor="w").grid(row=0, column=0, sticky="ew", padx=8)

        ctk.CTkButton(
            fila, text="Editar", width=80, command=lambda c=cargo: self._on_editar(c)
        ).grid(row=0, column=1, padx=4)
        if cargo.is_active:
            ctk.CTkButton(
                fila,
                text="Archivar",
                width=80,
                fg_color="#a85050",
                hover_color="#874242",
                command=lambda c=cargo: self._on_archivar(c),
            ).grid(row=0, column=2, padx=4)
        else:
            ctk.CTkButton(
                fila,
                text="Reactivar",
                width=80,
                command=lambda c=cargo: self._on_reactivar(c),
            ).grid(row=0, column=2, padx=4)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_agregar(self) -> None:
        nombre = self._nuevo_nombre_var.get().strip()
        if not nombre:
            messagebox.showwarning("Campo requerido", "Escriba el nombre del cargo.")
            return
        depto_label = self._nuevo_depto_label_var.get()
        depto_id = self._depto_label_to_id.get(depto_label)
        try:
            self._create_fn(nombre, departamento_id=depto_id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo crear", str(exc))
            return
        self._nuevo_nombre_var.set("")
        self._nuevo_depto_label_var.set(_GLOBAL_LABEL)
        self._refrescar()

    def _on_editar(self, cargo: Cargo) -> None:
        """Abre un mini-diálogo para renombrar y cambiar el departamento."""
        assert cargo.id is not None
        EditarCargoDialog(
            self,
            cargo=cargo,
            departamentos=list(self._depto_label_to_id.items()),
            rename_fn=self._rename_fn,
            set_departamento_fn=self._set_departamento_fn,
            on_done=self._refrescar,
        )

    def _on_archivar(self, cargo: Cargo) -> None:
        assert cargo.id is not None
        if not messagebox.askyesno(
            "Confirmar archivar",
            f"¿Archivar el cargo '{cargo.nombre}'?",
        ):
            return
        try:
            self._archive_fn(cargo.id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo archivar", str(exc))
            return
        self._refrescar()

    def _on_reactivar(self, cargo: Cargo) -> None:
        assert cargo.id is not None
        try:
            self._unarchive_fn(cargo.id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo reactivar", str(exc))
            return
        self._refrescar()


class EditarCargoDialog(ctk.CTkToplevel):
    """Diálogo modal para renombrar y/o cambiar el departamento del cargo."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        cargo: Cargo,
        departamentos: List[tuple[str, Optional[int]]],
        rename_fn: Callable[[int, str], None],
        set_departamento_fn: Callable[[int, Optional[int]], None],
        on_done: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        assert cargo.id is not None
        self._cargo = cargo
        self._rename_fn = rename_fn
        self._set_departamento_fn = set_departamento_fn
        self._on_done = on_done
        self._depto_map = dict(departamentos)

        self.title(f"Editar cargo — {cargo.nombre}")
        self.geometry("440x230")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Nombre:").grid(
            row=0, column=0, padx=(16, 8), pady=(20, 6), sticky="e"
        )
        self._nombre_var = ctk.StringVar(value=cargo.nombre)
        ctk.CTkEntry(self, textvariable=self._nombre_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 16), pady=(20, 6)
        )

        ctk.CTkLabel(self, text="Aplica a:").grid(row=1, column=0, padx=(16, 8), pady=6, sticky="e")
        actual_label = next(
            (lbl for lbl, dep_id in self._depto_map.items() if dep_id == cargo.departamento_id),
            _GLOBAL_LABEL,
        )
        self._depto_var = ctk.StringVar(value=actual_label)
        self._combo_depto = ctk.CTkOptionMenu(
            self,
            values=list(self._depto_map.keys()),
            variable=self._depto_var,
        )
        self._combo_depto.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=6)

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=2, column=0, columnspan=2, pady=(20, 16))
        ctk.CTkButton(botones, text="Cancelar", width=100, command=self.destroy).pack(
            side="left", padx=8
        )
        ctk.CTkButton(botones, text="Guardar", width=100, command=self._on_guardar).pack(
            side="left", padx=8
        )

        self.after(10, self._configurar_modal)

    def _configurar_modal(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

    def _on_guardar(self) -> None:
        assert self._cargo.id is not None
        nuevo_nombre = self._nombre_var.get().strip()
        if not nuevo_nombre:
            messagebox.showwarning("Campo requerido", "El nombre no puede estar vacío.")
            return
        nuevo_depto_id = self._depto_map.get(self._depto_var.get())
        try:
            if nuevo_nombre != self._cargo.nombre:
                self._rename_fn(self._cargo.id, nuevo_nombre)
            if nuevo_depto_id != self._cargo.departamento_id:
                self._set_departamento_fn(self._cargo.id, nuevo_depto_id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo guardar", str(exc))
            return
        self._on_done()
        self.destroy()
