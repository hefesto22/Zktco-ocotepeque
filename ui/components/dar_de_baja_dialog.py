"""Diálogo modal ``DarDeBajaDialog``.

Formulario para desactivar (dar de baja) un empleado. Es una acción
destructiva — archiva al empleado y cierra su turno vigente — por lo
que sigue el requisito del PRD de "confirmación explícita antes de
cualquier acción destructiva":

* Muestra un banner de advertencia explicando el efecto.
* El botón principal se llama "Confirmar baja" (no solo "Guardar") y
  se pinta con el color de peligro del tema.

El diálogo emite un payload con ``(fecha_baja, motivo_baja, nota_baja)``.
El service valida:

    * ``motivo_baja`` ∈ enum ``MotivoBaja``.
    * Si ``motivo_baja == 'OTRO'``, ``nota_baja`` obligatoria.
    * Fecha en formato ISO ``YYYY-MM-DD``.

El diálogo pre-valida SOLO la regla de nota-obligatoria-si-OTRO (es un
guard rápido para el usuario). El resto se delega al service.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Callable, Dict, List, Optional, Tuple, TypedDict, Union

import customtkinter as ctk

from core.models.empleado import MotivoBaja


class DarDeBajaPayload(TypedDict):
    """Payload del diálogo."""

    fecha_baja: str
    motivo_baja: str  # uno de ``MotivoBaja.*.value``
    nota_baja: Optional[str]


SubmitFn = Callable[[DarDeBajaPayload], Optional[str]]


# Orden visual + labels en español amigable para el combo.
# La clave que se persiste es el ``.value`` del enum (SCREAMING_SNAKE).
_MOTIVOS_LABELS: Tuple[Tuple[MotivoBaja, str], ...] = (
    (MotivoBaja.RENUNCIA, "Renuncia"),
    (MotivoBaja.DESPIDO, "Despido"),
    (MotivoBaja.FIN_CONTRATO, "Fin de contrato"),
    (MotivoBaja.JUBILACION, "Jubilación"),
    (MotivoBaja.FALLECIMIENTO, "Fallecimiento"),
    (MotivoBaja.OTRO, "Otro (requiere nota)"),
)


def _hoy_iso() -> str:
    """Fecha de hoy en formato ``YYYY-MM-DD``."""
    return _dt.date.today().isoformat()


class DarDeBajaDialog(ctk.CTkToplevel):
    """Diálogo modal para dar de baja a un empleado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        on_submit: SubmitFn,
        empleado_descripcion: str,
        initial_fecha_baja: Optional[str] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre; el diálogo se vuelve transient suyo.
            on_submit: Callback del caller. ``None`` = OK (cierra);
                ``str`` = error a mostrar (no cierra).
            empleado_descripcion: Texto corto identificando al empleado.
            initial_fecha_baja: Fecha precargada. Default = hoy.
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._log = logging.getLogger(self.__class__.__name__)

        self.title("Dar de baja")
        self.geometry("520x440")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        # Mapa label -> value del enum para recuperar el motivo al guardar.
        self._motivo_map: Dict[str, str] = {
            label: motivo.value for motivo, label in _MOTIVOS_LABELS
        }
        self._labels_ordenados: List[str] = [label for _m, label in _MOTIVOS_LABELS]

        self._construir_ui(
            empleado_descripcion=empleado_descripcion,
            initial_fecha_baja=initial_fecha_baja or _hoy_iso(),
        )

        self.after(10, self._configurar_modal)

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(
        self,
        empleado_descripcion: str,
        initial_fecha_baja: str,
    ) -> None:
        """Arma el formulario completo."""
        self.grid_columnconfigure(1, weight=1)
        pad_x = 16
        pad_y = 6
        row = 0

        # Cabecera: empleado.
        ctk.CTkLabel(
            self,
            text=f"Empleado: {empleado_descripcion}",
            anchor="w",
            wraplength=480,
            justify="left",
        ).grid(
            row=row,
            column=0,
            columnspan=4,
            sticky="ew",
            padx=pad_x,
            pady=(pad_x, pad_y),
        )
        row += 1

        # Banner de advertencia.
        warning = ctk.CTkLabel(
            self,
            text=(
                "⚠  Esta acción archiva al empleado y cierra su turno vigente. "
                "Se puede revertir reactivándolo, pero su historial de asignaciones "
                "no se restaura automáticamente."
            ),
            text_color=("#a25b00", "#ffb86c"),
            fg_color=("#fff4e0", "#3a2e1a"),
            corner_radius=6,
            anchor="w",
            wraplength=480,
            justify="left",
        )
        warning.grid(
            row=row,
            column=0,
            columnspan=4,
            sticky="ew",
            padx=pad_x,
            pady=(0, pad_y * 2),
            ipadx=8,
            ipady=8,
        )
        row += 1

        # Motivo.
        ctk.CTkLabel(self, text="Motivo:").grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._combo_motivo = ctk.CTkOptionMenu(
            self, values=self._labels_ordenados, command=self._on_motivo_change
        )
        self._combo_motivo.grid(
            row=row,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, pad_x),
            pady=pad_y,
        )
        row += 1

        # Fecha.
        ctk.CTkLabel(self, text="Fecha de baja:").grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_fecha = ctk.CTkEntry(self, placeholder_text="YYYY-MM-DD", width=140)
        self._entry_fecha.insert(0, initial_fecha_baja)
        self._entry_fecha.grid(row=row, column=1, sticky="w", pady=pad_y)
        row += 1

        # Nota (multilinea).
        ctk.CTkLabel(self, text="Nota:").grid(
            row=row, column=0, sticky="ne", padx=(pad_x, 8), pady=pad_y
        )
        self._text_nota = ctk.CTkTextbox(self, height=80, wrap="word")
        self._text_nota.grid(
            row=row,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, pad_x),
            pady=pad_y,
        )
        row += 1

        # Mensaje de error.
        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=480,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=row, column=0, columnspan=4, sticky="ew", padx=pad_x, pady=(pad_y, 4)
        )
        row += 1

        # Botones.
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=row, column=0, columnspan=4, sticky="e", padx=pad_x, pady=(4, pad_x))

        btn_cancel = ctk.CTkButton(
            botones,
            text="Cancelar",
            width=100,
            fg_color=("gray65", "gray35"),
            hover_color=("gray50", "gray45"),
            command=self._on_cancel,
        )
        btn_cancel.grid(row=0, column=0, padx=(0, 8))

        # Botón de confirmación con color de peligro (rojo).
        btn_confirm = ctk.CTkButton(
            botones,
            text="Confirmar baja",
            width=140,
            fg_color=("#b3261e", "#cf6679"),
            hover_color=("#8c1d18", "#a24a5a"),
            command=self._on_save,
        )
        btn_confirm.grid(row=0, column=1)

        self._entry_fecha.bind("<Return>", lambda _e: self._on_save())
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self._combo_motivo.focus_set()

    def _configurar_modal(self) -> None:
        """Activa el grab modal una vez la ventana es visible."""
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            self._log.debug("grab_set falló (ventana destruida)")

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_motivo_change(self, _label: str) -> None:
        """Callback del combo — limpia error si el usuario ya está corrigiendo."""
        self._mostrar_error("")

    def _on_cancel(self) -> None:
        """Cierra el diálogo sin invocar el callback."""
        self.destroy()

    def _on_save(self) -> None:
        """Arma el payload, invoca el callback y decide si cerrar."""
        payload_or_err = self._recolectar_payload()
        if isinstance(payload_or_err, str):
            self._mostrar_error(payload_or_err)
            return

        result = self._on_submit(payload_or_err)
        if result is None:
            self.destroy()
        else:
            self._mostrar_error(result)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _recolectar_payload(self) -> Union[DarDeBajaPayload, str]:
        """Lee widgets y devuelve el payload o un mensaje de error UI."""
        motivo_label = self._combo_motivo.get()
        if motivo_label not in self._motivo_map:
            return "Seleccione un motivo válido."
        motivo_value = self._motivo_map[motivo_label]

        fecha = self._entry_fecha.get().strip()
        if not fecha:
            return "Indique la fecha de baja."

        nota_raw = self._text_nota.get("1.0", "end").strip()
        nota_final: Optional[str] = nota_raw if nota_raw else None

        # Guard rápido para no ida y vuelta al service por este caso.
        if motivo_value == MotivoBaja.OTRO.value and not nota_final:
            return "El motivo 'Otro' requiere que escriba una nota."

        return DarDeBajaPayload(
            fecha_baja=fecha,
            motivo_baja=motivo_value,
            nota_baja=nota_final,
        )

    def _mostrar_error(self, mensaje: str) -> None:
        """Muestra el mensaje de error debajo del form."""
        self._label_error.configure(text=mensaje)
