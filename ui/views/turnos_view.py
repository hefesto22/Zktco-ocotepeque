"""Vista de Turnos (Sub-2.5).

Lista el catálogo de turnos con una fila por turno y abre el
``TurnoFormDialog`` para crear y editar. El permiso ``MANAGE_EMPLOYEES``
vive en el controller (cada método decorado) — esta vista solo arma
widgets e invoca callables del controller.

Diseño visual:

    ┌─────────────────────────────────────────────────────────────┐
    │  Turnos                                                      │
    ├─────────────────────────────────────────────────────────────┤
    │  [🔲 Ver archivados]          [Refrescar] [+ Nuevo turno]    │
    ├─────────────────────────────────────────────────────────────┤
    │  Admin 8-5 · 08:00–17:00 · Lun-Vie · 60min     [Ed] [Arch]  │
    │  Vigilancia · 22:00–06:00 · Sáb-Dom · 30min    [Ed] [Arch]  │
    │  ...                                                         │
    └─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import List

import customtkinter as ctk

from core.models.turno import (
    DIAS_FIN_DE_SEMANA,
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    Turno,
)
from core.services.errors import (
    DuplicateTurnoNombreError,
    InvalidBitmaskError,
    InvalidDescansoError,
    InvalidTimeError,
    MissingRequiredFieldError,
    TurnoNotFoundError,
)
from ui.components.turno_form_dialog import TurnoFormDialog, TurnoFormPayload
from ui.controllers.turnos_controller import TurnosController


# Orden visual (paralelo al del diálogo) para listar los días abreviados.
_DIAS_ABREV: List[str] = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_BITS_EN_ORDEN: List[int] = [1 << (6 - i) for i in range(7)]


class TurnosView(ctk.CTkFrame):
    """Vista raíz del módulo Turnos."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: TurnosController,
    ) -> None:
        """Construye la vista.

        Args:
            master: Contenedor Tk donde se monta.
            controller: Controller ya inyectado con session + servicio.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._log = logging.getLogger(self.__class__.__name__)

        self._ver_archivados_var = ctk.BooleanVar(value=False)

        self._construir_ui()
        self._refrescar()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma título, toolbar y lista scrollable."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Turnos",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))

        self._toolbar = self._construir_toolbar()
        self._toolbar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        self._lista = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._lista.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._lista.grid_columnconfigure(0, weight=1)

    def _construir_toolbar(self) -> ctk.CTkFrame:
        """Checkbox de archivados + botones Refrescar y Nuevo turno."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(0, weight=1)

        chk = ctk.CTkCheckBox(
            bar,
            text="Ver archivados",
            variable=self._ver_archivados_var,
            command=self._refrescar,
        )
        chk.grid(row=0, column=0, sticky="w")

        btn_refresh = ctk.CTkButton(bar, text="Refrescar", width=100, command=self._refrescar)
        btn_refresh.grid(row=0, column=1, sticky="e", padx=(0, 8))

        btn_nuevo = ctk.CTkButton(bar, text="+ Nuevo turno", width=140, command=self._on_nuevo)
        btn_nuevo.grid(row=0, column=2, sticky="e")

        return bar

    # ── Render de la lista ────────────────────────────────────────────────

    def _refrescar(self) -> None:
        """Lee del controller y re-pinta toda la lista."""
        for hijo in self._lista.winfo_children():
            hijo.destroy()

        solo_activos = not self._ver_archivados_var.get()
        try:
            turnos = self._controller.list_turnos(solo_activos=solo_activos)
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando turnos")
            messagebox.showerror("Error", "No se pudo cargar la lista de turnos.")
            return

        if not turnos:
            self._pintar_vacio()
            return

        for idx, turno in enumerate(turnos):
            self._pintar_fila(idx, turno)

    def _pintar_vacio(self) -> None:
        """Muestra un placeholder cuando la lista está vacía."""
        msg = ctk.CTkLabel(
            self._lista,
            text="No hay turnos para mostrar.",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
        )
        msg.grid(row=0, column=0, sticky="w", padx=12, pady=12)

    def _pintar_fila(self, idx: int, turno: Turno) -> None:
        """Dibuja una fila para un turno (activo o archivado)."""
        fila = ctk.CTkFrame(self._lista, fg_color=("gray90", "gray20"))
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)

        texto = self._formato_resumen(turno)
        if not turno.is_active:
            texto = f"{texto}  (archivado)"
        color = ("gray10", "gray90") if turno.is_active else ("gray45", "gray55")

        label = ctk.CTkLabel(fila, text=texto, text_color=color, anchor="w")
        label.grid(row=0, column=0, sticky="ew", padx=12, pady=8)

        if turno.id is None:
            return

        if turno.is_active:
            btn_edit = ctk.CTkButton(
                fila, text="Editar", width=90, command=lambda t=turno: self._on_editar(t)
            )
            btn_edit.grid(row=0, column=1, padx=4, pady=4)

            btn_arch = ctk.CTkButton(
                fila,
                text="Archivar",
                width=90,
                fg_color=("gray65", "gray35"),
                hover_color=("gray50", "gray45"),
                command=lambda t=turno: self._on_archivar(t),
            )
            btn_arch.grid(row=0, column=2, padx=4, pady=4)
        else:
            btn_react = ctk.CTkButton(
                fila, text="Reactivar", width=90, command=lambda t=turno: self._on_reactivar(t)
            )
            btn_react.grid(row=0, column=1, padx=4, pady=4)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_nuevo(self) -> None:
        """Abre el diálogo con valores default."""
        dialog = TurnoFormDialog(
            self,
            title="Nuevo turno",
            on_submit=self._handle_create,
        )
        self.wait_window(dialog)
        self._refrescar()

    def _on_editar(self, turno: Turno) -> None:
        """Abre el diálogo precargado con los valores del turno."""
        assert turno.id is not None
        turno_id = turno.id

        def submit(payload: TurnoFormPayload) -> str | None:
            return self._handle_update(turno_id, payload)

        dialog = TurnoFormDialog(
            self,
            title=f"Editar turno — {turno.nombre}",
            on_submit=submit,
            initial_nombre=turno.nombre,
            initial_hora_entrada=turno.hora_entrada,
            initial_hora_salida=turno.hora_salida,
            initial_minutos_descanso=turno.minutos_descanso,
            initial_dias_semana=turno.dias_semana,
        )
        self.wait_window(dialog)
        self._refrescar()

    def _on_archivar(self, turno: Turno) -> None:
        """Confirma y archiva un turno."""
        assert turno.id is not None
        confirmado = messagebox.askyesno(
            "Archivar turno",
            (
                f'¿Archivar "{turno.nombre}"?\n\n'
                "El turno no aparecerá al asignar empleados, pero las "
                "asignaciones vigentes se mantienen."
            ),
        )
        if not confirmado:
            return
        try:
            self._controller.archive_turno(turno.id)
        except TurnoNotFoundError as exc:
            messagebox.showerror("Turno no encontrado", str(exc))
        except Exception:  # noqa: BLE001
            self._log.exception("Error archivando turno id=%s", turno.id)
            messagebox.showerror("Error", "No se pudo archivar el turno.")
            return
        self._refrescar()

    def _on_reactivar(self, turno: Turno) -> None:
        """Reactiva un turno archivado."""
        assert turno.id is not None
        try:
            self._controller.unarchive_turno(turno.id)
        except TurnoNotFoundError as exc:
            messagebox.showerror("Turno no encontrado", str(exc))
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando turno id=%s", turno.id)
            messagebox.showerror("Error", "No se pudo reactivar el turno.")
            return
        self._refrescar()

    # ── Callbacks del diálogo ─────────────────────────────────────────────

    def _handle_create(self, payload: TurnoFormPayload) -> str | None:
        """Invoca create_turno. Devuelve None si OK, mensaje si falla."""
        try:
            self._controller.create_turno(**payload)
        except (
            MissingRequiredFieldError,
            InvalidTimeError,
            InvalidBitmaskError,
            InvalidDescansoError,
            DuplicateTurnoNombreError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error creando turno")
            return "Ocurrió un error inesperado al crear el turno."
        return None

    def _handle_update(self, turno_id: int, payload: TurnoFormPayload) -> str | None:
        """Invoca update_turno. Devuelve None si OK, mensaje si falla."""
        try:
            self._controller.update_turno(turno_id=turno_id, **payload)
        except (
            MissingRequiredFieldError,
            InvalidTimeError,
            InvalidBitmaskError,
            InvalidDescansoError,
            DuplicateTurnoNombreError,
            TurnoNotFoundError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error actualizando turno id=%s", turno_id)
            return "Ocurrió un error inesperado al guardar el turno."
        return None

    # ── Helpers de formato ────────────────────────────────────────────────

    @staticmethod
    def _formato_resumen(turno: Turno) -> str:
        """Resume un turno en una línea legible para la lista.

        Formato: ``"Nombre · HH:MM–HH:MM · Días · Xmin descanso"`` con
        ``(cruza medianoche)`` agregado si aplica.
        """
        dias_txt = TurnosView._formato_dias(turno.dias_semana)
        horario = f"{turno.hora_entrada}–{turno.hora_salida}"
        if turno.cruza_medianoche:
            horario += " (cruza medianoche)"
        return (
            f"{turno.nombre}  ·  {horario}  ·  {dias_txt}  ·  "
            f"{turno.minutos_descanso}min descanso"
        )

    @staticmethod
    def _formato_dias(bitmask: int) -> str:
        """Formatea un bitmask de días en texto legible.

        Casos especiales: toda la semana, laborales (L-V) y fin de
        semana (S-D) tienen texto propio. El resto lista abreviaciones.
        """
        if bitmask == DIAS_TODA_LA_SEMANA:
            return "Toda la semana"
        if bitmask == DIAS_LABORALES:
            return "Lun-Vie"
        if bitmask == DIAS_FIN_DE_SEMANA:
            return "Sáb-Dom"

        activos = [_DIAS_ABREV[i] for i, bit in enumerate(_BITS_EN_ORDEN) if bitmask & bit]
        return ", ".join(activos) if activos else "—"
