"""Diálogo modal ``TurnoFormDialog``.

Formulario reutilizable para crear y editar turnos. Sirve el mismo
diálogo tanto para "Nuevo turno" (valores default) como para "Editar
turno X" (valores precargados desde un ``Turno`` existente).

Diseño del API:

    El diálogo NO llama directamente al controller. Recibe un callback
    ``on_submit(payload)`` que el caller usa para invocar el service:

        - Si el callback devuelve ``None`` → operación exitosa, el
          diálogo se cierra.
        - Si devuelve un ``str`` → se muestra como mensaje de error en
          rojo dentro del diálogo, los campos se preservan, el usuario
          corrige y reintenta.

    Este pattern permite que validaciones de formato (``InvalidTimeError``,
    ``InvalidBitmaskError``) y de negocio (``DuplicateTurnoNombreError``,
    ``InvalidDescansoError``) se muestren in-dialog sin cerrar la ventana.

Diseño visual (approx 460x340 px):

    ┌─ Nuevo turno ─────────────────────────────┐
    │  Nombre:          [_______________]        │
    │  Hora entrada:    [__:__]                  │
    │  Hora salida:     [__:__]                  │
    │  Descanso (min):  [_____]                  │
    │                                            │
    │  Días:  [x]Lun [x]Mar [x]Mié [x]Jue        │
    │         [x]Vie [ ]Sáb [ ]Dom               │
    │                                            │
    │  (mensaje de error en rojo, si aplica)     │
    │                                            │
    │                     [Cancelar]  [Guardar]  │
    └────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple, TypedDict

import customtkinter as ctk

from core.models.turno import (
    DIAS_LABORALES,
    DOMINGO,
    JUEVES,
    LUNES,
    MARTES,
    MIERCOLES,
    SABADO,
    VIERNES,
)


class TurnoFormPayload(TypedDict):
    """Resultado del form al pulsar Guardar (todos los campos editables)."""

    nombre: str
    hora_entrada: str
    hora_salida: str
    minutos_descanso: int
    dias_semana: int


# Callback del caller: recibe el payload y decide si cerrar o mostrar error.
# Devuelve None en éxito (cierra diálogo) o ``str`` con el mensaje de error.
SubmitFn = Callable[[TurnoFormPayload], Optional[str]]


# Orden visual de los días (Lunes → Domingo, según el bitmask del modelo).
_DIAS_ORDER: Tuple[Tuple[int, str], ...] = (
    (LUNES, "Lun"),
    (MARTES, "Mar"),
    (MIERCOLES, "Mié"),
    (JUEVES, "Jue"),
    (VIERNES, "Vie"),
    (SABADO, "Sáb"),
    (DOMINGO, "Dom"),
)


class TurnoFormDialog(ctk.CTkToplevel):
    """Diálogo modal para crear o editar un turno."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        initial_nombre: str = "",
        initial_hora_entrada: str = "08:00",
        initial_hora_salida: str = "17:00",
        initial_minutos_descanso: int = 60,
        initial_dias_semana: int = DIAS_LABORALES,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre — el diálogo se vuelve transient suyo.
            title: Texto de la barra de título (ej. "Nuevo turno" /
                "Editar turno").
            on_submit: Callback que el caller implementa para invocar el
                controller. Devuelve ``None`` si OK (cierra) o ``str``
                con el mensaje a mostrar si falla.
            initial_nombre: Valor precargado del nombre. Vacío = nuevo.
            initial_hora_entrada: Formato ``"HH:MM"``.
            initial_hora_salida: Formato ``"HH:MM"``.
            initial_minutos_descanso: Entero >= 0.
            initial_dias_semana: Bitmask 1..127 (default L-V).
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._log = logging.getLogger(self.__class__.__name__)

        self.title(title)
        self.geometry("480x380")
        self.resizable(False, False)
        # Lo hacemos transient al parent: minimizar el parent minimiza éste.
        self.transient(parent.winfo_toplevel())

        self._construir_ui(
            initial_nombre,
            initial_hora_entrada,
            initial_hora_salida,
            initial_minutos_descanso,
            initial_dias_semana,
        )

        # grab_set tiene que correr después de que la ventana sea visible.
        self.after(10, self._configurar_modal)

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(
        self,
        ini_nombre: str,
        ini_entrada: str,
        ini_salida: str,
        ini_descanso: int,
        ini_dias: int,
    ) -> None:
        """Arma el formulario completo."""
        self.grid_columnconfigure(1, weight=1)

        pad_x = 16
        pad_y = 6

        # Fila 0: Nombre
        ctk.CTkLabel(self, text="Nombre:").grid(
            row=0, column=0, sticky="e", padx=(pad_x, 8), pady=(pad_x, pad_y)
        )
        self._entry_nombre = ctk.CTkEntry(self, placeholder_text="Ej: Administrativo 8-5")
        self._entry_nombre.insert(0, ini_nombre)
        self._entry_nombre.grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(0, pad_x), pady=(pad_x, pad_y)
        )

        # Fila 1: Hora entrada
        ctk.CTkLabel(self, text="Hora entrada:").grid(
            row=1, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_entrada = ctk.CTkEntry(self, placeholder_text="HH:MM", width=80)
        self._entry_entrada.insert(0, ini_entrada)
        self._entry_entrada.grid(row=1, column=1, sticky="w", pady=pad_y)

        # Fila 2: Hora salida
        ctk.CTkLabel(self, text="Hora salida:").grid(
            row=2, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_salida = ctk.CTkEntry(self, placeholder_text="HH:MM", width=80)
        self._entry_salida.insert(0, ini_salida)
        self._entry_salida.grid(row=2, column=1, sticky="w", pady=pad_y)

        # Fila 3: Descanso
        ctk.CTkLabel(self, text="Descanso (min):").grid(
            row=3, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_descanso = ctk.CTkEntry(self, placeholder_text="0", width=80)
        self._entry_descanso.insert(0, str(ini_descanso))
        self._entry_descanso.grid(row=3, column=1, sticky="w", pady=pad_y)

        # Fila 4-5: Días (checkboxes en 2 filas de 4+3)
        ctk.CTkLabel(self, text="Días:").grid(
            row=4, column=0, sticky="ne", padx=(pad_x, 8), pady=(pad_y, 2)
        )
        dias_frame = ctk.CTkFrame(self, fg_color="transparent")
        dias_frame.grid(row=4, column=1, columnspan=3, sticky="w", pady=(pad_y, 2))
        self._dia_vars: List[ctk.BooleanVar] = []
        for idx, (bit, label_dia) in enumerate(_DIAS_ORDER):
            var = ctk.BooleanVar(value=bool(ini_dias & bit))
            self._dia_vars.append(var)
            chk = ctk.CTkCheckBox(dias_frame, text=label_dia, variable=var, width=60)
            chk.grid(row=idx // 4, column=idx % 4, padx=4, pady=2, sticky="w")

        # Fila 6: Mensaje de error (inicialmente oculto).
        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=420,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=6, column=0, columnspan=4, sticky="ew", padx=pad_x, pady=(pad_y, 4)
        )

        # Fila 7: Botones.
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=7, column=0, columnspan=4, sticky="e", padx=pad_x, pady=(4, pad_x))

        btn_cancel = ctk.CTkButton(
            botones,
            text="Cancelar",
            width=100,
            fg_color=("gray65", "gray35"),
            hover_color=("gray50", "gray45"),
            command=self._on_cancel,
        )
        btn_cancel.grid(row=0, column=0, padx=(0, 8))

        btn_save = ctk.CTkButton(botones, text="Guardar", width=100, command=self._on_save)
        btn_save.grid(row=0, column=1)

        # Enter dispara Guardar desde cualquier entry.
        for entry in (
            self._entry_nombre,
            self._entry_entrada,
            self._entry_salida,
            self._entry_descanso,
        ):
            entry.bind("<Return>", lambda _e: self._on_save())

        # Esc cierra sin guardar.
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self._entry_nombre.focus_set()

    def _configurar_modal(self) -> None:
        """Activa el grab modal una vez la ventana es visible."""
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 — si el widget ya fue destruido
            self._log.debug("grab_set falló (ventana destruida)")

    # ── Handlers ──────────────────────────────────────────────────────────

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

    def _recolectar_payload(self) -> TurnoFormPayload | str:
        """Lee los widgets y devuelve el payload o un mensaje de error.

        Solo valida las conversiones de tipo que la UI debe proteger
        (descanso como entero). El resto de reglas semánticas las
        impone el service — no duplicamos validación.
        """
        descanso_str = self._entry_descanso.get().strip()
        try:
            descanso = int(descanso_str)
        except ValueError:
            return "Descanso debe ser un número entero."
        if descanso < 0:
            return "Descanso no puede ser negativo."

        dias = self._calcular_bitmask_dias()
        if dias == 0:
            return "Seleccione al menos un día de la semana."

        return TurnoFormPayload(
            nombre=self._entry_nombre.get().strip(),
            hora_entrada=self._entry_entrada.get().strip(),
            hora_salida=self._entry_salida.get().strip(),
            minutos_descanso=descanso,
            dias_semana=dias,
        )

    def _calcular_bitmask_dias(self) -> int:
        """Traduce el estado de los 7 checkboxes al bitmask del modelo."""
        total = 0
        for (bit, _), var in zip(_DIAS_ORDER, self._dia_vars):
            if var.get():
                total |= bit
        return total

    def _mostrar_error(self, mensaje: str) -> None:
        """Muestra el mensaje de error debajo del form."""
        self._label_error.configure(text=mensaje)
