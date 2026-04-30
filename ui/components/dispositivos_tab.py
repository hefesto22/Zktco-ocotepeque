"""Tab dedicado ``DispositivosTab`` (Plan B / Sub-2.4b).

Tab para administrar el catálogo de dispositivos ZKTeco. NO reusa
``CatalogoTab`` porque éste asume una entidad ``(id, nombre, is_active)``
con un solo campo editable; ``Dispositivo`` agrega ``ip`` y ``puerto``,
que requieren un formulario completo (``DispositivoFormDialog``) y un
render de fila distinto ("Sede — 192.168.0.101:4370").

Diseño visual:

    ┌───────────────────────────────────────────────────────┐
    │  [🔲 Ver archivados]                      [Refrescar]  │
    ├───────────────────────────────────────────────────────┤
    │  Scrollable frame:                                    │
    │    · Sede Principal                                   │
    │      192.168.0.101:4370    [Editar] [Archivar]        │
    │    · Bodega (archivado)                               │
    │      10.0.0.5:4370         [Reactivar]                │
    ├───────────────────────────────────────────────────────┤
    │                              [+ Nuevo dispositivo]    │
    └───────────────────────────────────────────────────────┘

Responsabilidad: solo render + interacción. NO conoce SQLite ni
``DispositivoConfigService``. Toda I/O pasa por callables inyectados
desde la vista contenedora — patrón ya establecido en Sub-2.4 y 2.5.
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Callable, List

import customtkinter as ctk

from core.models.dispositivo import Dispositivo
from core.services.errors import (
    DispositivoNotFoundError,
    DuplicateDispositivoEndpointError,
    DuplicateDispositivoNombreError,
    InvalidIPError,
    InvalidPuertoError,
    MissingRequiredFieldError,
)
from ui.components.dispositivo_form_dialog import (
    DispositivoFormDialog,
    DispositivoFormPayload,
)

# Callables inyectados desde el controller — tipados estrechos.
ListFn = Callable[[bool], List[Dispositivo]]
CreateFn = Callable[[str, str, int], Dispositivo]
UpdateFn = Callable[[int, str, str, int], None]
ArchiveFn = Callable[[int], None]
UnarchiveFn = Callable[[int], None]


class DispositivosTab(ctk.CTkFrame):
    """Tab para administrar el catálogo de dispositivos ZKTeco."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        list_fn: ListFn,
        create_fn: CreateFn,
        update_fn: UpdateFn,
        archive_fn: ArchiveFn,
        unarchive_fn: UnarchiveFn,
    ) -> None:
        """Construye el tab.

        Args:
            master: Contenedor Tk donde se monta.
            list_fn: ``fn(solo_activos)`` → lista de dispositivos.
            create_fn: ``fn(nombre, ip, puerto)`` → dispositivo creado.
            update_fn: ``fn(id, nombre, ip, puerto)``.
            archive_fn: ``fn(id)``.
            unarchive_fn: ``fn(id)``.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._list_fn = list_fn
        self._create_fn = create_fn
        self._update_fn = update_fn
        self._archive_fn = archive_fn
        self._unarchive_fn = unarchive_fn
        self._log = logging.getLogger(self.__class__.__name__)

        # Estado UI: toggle "Ver archivados" arranca apagado.
        self._ver_archivados_var = ctk.BooleanVar(value=False)

        self._construir_ui()
        self._refrescar()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma toolbar + lista + barra inferior con botón Nuevo."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._toolbar = self._construir_toolbar()
        self._toolbar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))

        self._lista = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._lista.grid(row=1, column=0, sticky="nsew", padx=16, pady=0)
        self._lista.grid_columnconfigure(0, weight=1)

        self._form = self._construir_form()
        self._form.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))

    def _construir_toolbar(self) -> ctk.CTkFrame:
        """Checkbox de archivados + botón refrescar."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(0, weight=1)

        chk = ctk.CTkCheckBox(
            bar,
            text="Ver archivados",
            variable=self._ver_archivados_var,
            command=self._refrescar,
        )
        chk.grid(row=0, column=0, sticky="w")

        btn_refresh = ctk.CTkButton(
            bar,
            text="Refrescar",
            width=100,
            command=self._refrescar,
        )
        btn_refresh.grid(row=0, column=1, sticky="e")

        return bar

    def _construir_form(self) -> ctk.CTkFrame:
        """Barra inferior con un único botón "Nuevo dispositivo"."""
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid_columnconfigure(0, weight=1)

        btn_nuevo = ctk.CTkButton(
            form,
            text="+ Nuevo dispositivo",
            width=180,
            command=self._on_nuevo,
        )
        btn_nuevo.grid(row=0, column=1, sticky="e")

        return form

    # ── Render de la lista ────────────────────────────────────────────────

    def _refrescar(self) -> None:
        """Lee del controller y vuelve a pintar toda la lista."""
        for hijo in self._lista.winfo_children():
            hijo.destroy()

        solo_activos = not self._ver_archivados_var.get()
        try:
            items = self._list_fn(solo_activos)
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando dispositivos")
            messagebox.showerror(
                "Error",
                "No se pudo cargar la lista de dispositivos.",
            )
            return

        if not items:
            self._pintar_vacio()
            return

        for idx, item in enumerate(items):
            self._pintar_fila(idx, item)

    def _pintar_vacio(self) -> None:
        """Muestra un placeholder cuando la lista está vacía."""
        msg = ctk.CTkLabel(
            self._lista,
            text="No hay dispositivos para mostrar.",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
        )
        msg.grid(row=0, column=0, sticky="w", padx=12, pady=12)

    def _pintar_fila(self, idx: int, disp: Dispositivo) -> None:
        """Dibuja una fila para un dispositivo activo o archivado."""
        fila = ctk.CTkFrame(self._lista, fg_color=("gray90", "gray20"))
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)

        nombre_texto = disp.nombre if disp.is_active else f"{disp.nombre}  (archivado)"
        endpoint_texto = f"{disp.ip}:{disp.puerto}"
        color_principal = ("gray10", "gray90") if disp.is_active else ("gray45", "gray55")
        color_secundario = ("gray30", "gray70") if disp.is_active else ("gray50", "gray50")

        # Bloque de texto (nombre + endpoint debajo).
        texto = ctk.CTkFrame(fila, fg_color="transparent")
        texto.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        texto.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            texto,
            text=nombre_texto,
            text_color=color_principal,
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            texto,
            text=endpoint_texto,
            text_color=color_secundario,
            anchor="w",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="ew")

        if disp.id is None:
            return  # Defensa: no operamos sobre filas sin id (imposible en práctica).

        if disp.is_active:
            btn_editar = ctk.CTkButton(
                fila,
                text="Editar",
                width=100,
                command=lambda d=disp: self._on_editar(d),
            )
            btn_editar.grid(row=0, column=1, padx=4, pady=4)

            btn_archive = ctk.CTkButton(
                fila,
                text="Archivar",
                width=100,
                fg_color=("gray65", "gray35"),
                hover_color=("gray50", "gray45"),
                command=lambda d=disp: self._on_archivar(d),
            )
            btn_archive.grid(row=0, column=2, padx=4, pady=4)
        else:
            btn_unarchive = ctk.CTkButton(
                fila,
                text="Reactivar",
                width=100,
                command=lambda d=disp: self._on_reactivar(d),
            )
            btn_unarchive.grid(row=0, column=1, padx=4, pady=4)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_nuevo(self) -> None:
        """Click en "+ Nuevo dispositivo" — abre el form modal."""

        def submit(payload: DispositivoFormPayload) -> str | None:
            return self._intentar_crear(payload)

        DispositivoFormDialog(
            self,
            title="Nuevo dispositivo",
            on_submit=submit,
        )

    def _on_editar(self, disp: Dispositivo) -> None:
        """Click en "Editar" — abre el form modal con valores precargados."""
        assert disp.id is not None
        disp_id = disp.id  # narrow para que mypy no pierda el tipo en el closure

        def submit(payload: DispositivoFormPayload) -> str | None:
            return self._intentar_actualizar(disp_id, payload)

        DispositivoFormDialog(
            self,
            title=f"Editar dispositivo — {disp.nombre}",
            on_submit=submit,
            initial_nombre=disp.nombre,
            initial_ip=disp.ip,
            initial_puerto=disp.puerto,
        )

    def _on_archivar(self, disp: Dispositivo) -> None:
        """Click en "Archivar" — confirma y archiva."""
        assert disp.id is not None
        confirmado = messagebox.askyesno(
            "Archivar dispositivo",
            (
                f'¿Archivar el dispositivo "{disp.nombre}" '
                f"({disp.ip}:{disp.puerto})?\n\n"
                "Sus registros históricos se preservan, pero el "
                "dispositivo dejará de aparecer en la sincronización. "
                "Puede reactivarlo en cualquier momento."
            ),
        )
        if not confirmado:
            return
        try:
            self._archive_fn(disp.id)
        except DispositivoNotFoundError as exc:
            messagebox.showerror("No encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error archivando dispositivo id=%s", disp.id)
            messagebox.showerror("Error", "No se pudo archivar el dispositivo.")
            return
        self._refrescar()

    def _on_reactivar(self, disp: Dispositivo) -> None:
        """Click en "Reactivar" sobre una fila archivada."""
        assert disp.id is not None
        try:
            self._unarchive_fn(disp.id)
        except DispositivoNotFoundError as exc:
            messagebox.showerror("No encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando dispositivo id=%s", disp.id)
            messagebox.showerror("Error", "No se pudo reactivar el dispositivo.")
            return
        self._refrescar()

    # ── Lógica de submit del form modal ───────────────────────────────────

    def _intentar_crear(self, payload: DispositivoFormPayload) -> str | None:
        """Intenta crear un dispositivo y traduce errores a string.

        Returns:
            ``None`` si OK (el modal se cierra y se refresca la lista).
            Mensaje en español si falla — el modal lo muestra inline y
            el usuario puede corregir sin perder los valores tipeados.
        """
        try:
            self._create_fn(payload["nombre"], payload["ip"], payload["puerto"])
        except (
            MissingRequiredFieldError,
            InvalidIPError,
            InvalidPuertoError,
            DuplicateDispositivoNombreError,
            DuplicateDispositivoEndpointError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado creando dispositivo")
            return "No se pudo crear el dispositivo. Revise los campos e intente de nuevo."
        self._refrescar()
        return None

    def _intentar_actualizar(
        self,
        disp_id: int,
        payload: DispositivoFormPayload,
    ) -> str | None:
        """Intenta actualizar un dispositivo y traduce errores a string."""
        try:
            self._update_fn(disp_id, payload["nombre"], payload["ip"], payload["puerto"])
        except (
            MissingRequiredFieldError,
            InvalidIPError,
            InvalidPuertoError,
            DuplicateDispositivoNombreError,
            DuplicateDispositivoEndpointError,
            DispositivoNotFoundError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado actualizando dispositivo id=%s", disp_id)
            return "No se pudo actualizar el dispositivo. Revise los campos e intente de nuevo."
        self._refrescar()
        return None
