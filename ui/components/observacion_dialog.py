"""Diálogo modal ``ObservacionDialog`` (Sub-3.4b).

Formulario para editar la observación manual de una fila de asistencia.
Es un diálogo simple: un ``CTkTextbox`` multilínea + botones Cancelar /
Guardar. Siguiendo el patrón de ``TurnoFormDialog`` de Sub-2.5, NO llama
directo al controller — recibe un callback ``on_submit`` que la vista
implementa para invocar el service.

API del callback:
    on_submit(texto: Optional[str]) -> Optional[str]

    - El diálogo entrega ``texto`` ya recortado (``strip()``), y vacío →
      ``None`` para ahorrarle al service la normalización.
    - Si el callback devuelve ``None`` → operación exitosa, el diálogo
      se cierra.
    - Si devuelve un ``str`` → mensaje de error en rojo in-dialog. Los
      campos se preservan, el usuario corrige y reintenta.

Diseño visual (approx 480x260 px):

    ┌─ Editar observación ──────────────────────────┐
    │  Fila: 2026-04-15 — Pérez Juan                │
    │                                                │
    │  ┌────────────────────────────────────────┐   │
    │  │ [textbox multilínea, 5 líneas]         │   │
    │  │                                        │   │
    │  └────────────────────────────────────────┘   │
    │                                                │
    │  (mensaje de error en rojo, si aplica)         │
    │                                                │
    │                    [Cancelar]  [Guardar]       │
    └────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import customtkinter as ctk


# Callback del caller: recibe el texto normalizado y decide si cerrar
# o mostrar error. Devuelve None en éxito, str con el mensaje si falla.
SubmitFn = Callable[[Optional[str]], Optional[str]]


class ObservacionDialog(ctk.CTkToplevel):
    """Diálogo modal para editar la observación de una asistencia."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        subtitle: str,
        on_submit: SubmitFn,
        initial_text: Optional[str] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre — el diálogo se vuelve transient suyo.
            subtitle: Texto contextual bajo el título, típicamente
                ``"fecha — empleado"`` para que el usuario sepa qué fila
                está editando.
            on_submit: Callback invocado al presionar Guardar.
            initial_text: Observación actual (precargada en el textbox).
                ``None`` o vacío = textbox vacío.
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._log = logging.getLogger(self.__class__.__name__)

        self.title("Editar observación")
        self.geometry("500x280")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self._construir_ui(subtitle, initial_text or "")
        # grab_set debe correr después de que la ventana sea visible.
        self.after(10, self._configurar_modal)

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(self, subtitle: str, initial_text: str) -> None:
        """Arma el formulario completo."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        pad_x = 16

        # Subtítulo contextual (qué fila estamos editando).
        lbl_sub = ctk.CTkLabel(
            self,
            text=subtitle,
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=460,
            justify="left",
        )
        lbl_sub.grid(row=0, column=0, sticky="ew", padx=pad_x, pady=(pad_x, 4))

        lbl_campo = ctk.CTkLabel(self, text="Observación:", anchor="w")
        lbl_campo.grid(row=1, column=0, sticky="w", padx=pad_x, pady=(4, 2))

        self._textbox = ctk.CTkTextbox(self, height=110, wrap="word")
        self._textbox.grid(row=2, column=0, sticky="nsew", padx=pad_x, pady=(0, 4))
        if initial_text:
            self._textbox.insert("1.0", initial_text)

        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=460,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(row=3, column=0, sticky="ew", padx=pad_x, pady=(2, 4))

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=4, column=0, sticky="e", padx=pad_x, pady=(4, pad_x))

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

        # Esc cancela, Ctrl+Enter guarda (Enter solo inserta newline en el textbox).
        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.bind("<Control-Return>", lambda _e: self._on_save())
        self._textbox.focus_set()

    def _configurar_modal(self) -> None:
        """Activa el grab modal una vez la ventana es visible."""
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 — el widget ya fue destruido
            self._log.debug("grab_set falló (ventana destruida)")

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_cancel(self) -> None:
        """Cierra el diálogo sin invocar el callback."""
        self.destroy()

    def _on_save(self) -> None:
        """Lee el textbox, invoca el callback y decide si cerrar."""
        texto_normalizado = self._leer_texto()
        result = self._on_submit(texto_normalizado)
        if result is None:
            self.destroy()
        else:
            self._mostrar_error(result)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _leer_texto(self) -> Optional[str]:
        """Lee el textbox y normaliza vacío/whitespace a ``None``."""
        # CTkTextbox.get("1.0", "end") incluye un '\n' final que tkinter
        # agrega — lo recortamos con strip(). Si el usuario solo tipeó
        # espacios o saltos de línea, lo tratamos como "sin observación".
        raw = self._textbox.get("1.0", "end").strip()
        return raw if raw else None

    def _mostrar_error(self, mensaje: str) -> None:
        """Muestra el mensaje de error debajo del textbox."""
        self._label_error.configure(text=mensaje)
