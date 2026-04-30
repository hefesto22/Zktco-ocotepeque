"""Diálogo modal ``ResetPasswordDialog`` (Sub-2.7a).

Reset rápido de la contraseña de un usuario. UI mínima:

    ┌─ Resetear contraseña — username ────────────┐
    │  Nueva contraseña:   [_______________]       │
    │  Confirmar:          [_______________]       │
    │                                              │
    │  (mensaje de error en rojo, si aplica)       │
    │                                              │
    │                  [Cancelar]  [Guardar]       │
    └──────────────────────────────────────────────┘

El callback recibe ``(new_password, confirm)`` y devuelve None=OK
o str=mensaje de error in-dialog. La validación de policy y bcrypt
viven en el service.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import customtkinter as ctk

# (new_password, confirm) -> None ok / str mensaje in-dialog
SubmitFn = Callable[[str, str], Optional[str]]


class ResetPasswordDialog(ctk.CTkToplevel):
    """Diálogo modal para resetear la contraseña de un usuario."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        username_objetivo: str,
        on_submit: SubmitFn,
    ) -> None:
        super().__init__(parent)
        self._on_submit = on_submit
        self._log = logging.getLogger(self.__class__.__name__)

        self.title(f"Resetear contraseña — {username_objetivo}")
        self.geometry("440x260")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self._construir_ui(username_objetivo)
        self.after(10, self._configurar_modal)

    def _construir_ui(self, username: str) -> None:
        self.grid_columnconfigure(1, weight=1)
        pad_x = 16
        pad_y = 6

        ctk.CTkLabel(
            self,
            text=f'Cambiar contraseña de "{username}"',
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=pad_x, pady=(pad_x, pad_y))

        ctk.CTkLabel(self, text="Nueva contraseña:").grid(
            row=1, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_pwd = ctk.CTkEntry(self, show="*", placeholder_text="Mínimo 8 caracteres")
        self._entry_pwd.grid(row=1, column=1, sticky="ew", padx=(0, pad_x), pady=pad_y)

        ctk.CTkLabel(self, text="Confirmar:").grid(
            row=2, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_confirm = ctk.CTkEntry(self, show="*", placeholder_text="Repita la contraseña")
        self._entry_confirm.grid(row=2, column=1, sticky="ew", padx=(0, pad_x), pady=pad_y)

        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=400,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=pad_x, pady=(pad_y, 4)
        )

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=4, column=0, columnspan=2, sticky="e", padx=pad_x, pady=(4, pad_x))
        ctk.CTkButton(
            botones,
            text="Cancelar",
            width=100,
            fg_color=("gray65", "gray35"),
            hover_color=("gray50", "gray45"),
            command=self._on_cancel,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(botones, text="Guardar", width=100, command=self._on_save).grid(
            row=0, column=1
        )

        self.bind("<Escape>", lambda _e: self._on_cancel())
        for entry in (self._entry_pwd, self._entry_confirm):
            entry.bind("<Return>", lambda _e: self._on_save())
        self._entry_pwd.focus_set()

    def _configurar_modal(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            self._log.debug("grab_set falló (ventana destruida)")

    def _on_cancel(self) -> None:
        self.destroy()

    def _on_save(self) -> None:
        pwd = self._entry_pwd.get()
        confirm = self._entry_confirm.get()
        if not pwd:
            self._label_error.configure(text="La contraseña es obligatoria.")
            return
        if pwd != confirm:
            self._label_error.configure(text="La contraseña y su confirmación no coinciden.")
            return
        result = self._on_submit(pwd, confirm)
        if result is None:
            self.destroy()
        else:
            self._label_error.configure(text=result)
