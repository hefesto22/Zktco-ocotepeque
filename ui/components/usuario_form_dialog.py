"""Diálogo modal ``UsuarioFormDialog`` (Sub-2.7a).

Formulario para crear o editar un usuario. Sirve dos modos:

    Modo "Nuevo":
        - Campos: username + full_name + rol (combo) + password + confirmar.
        - Username y password obligatorios y editables.

    Modo "Editar":
        - Campos: full_name + rol (combo).
        - Username NO editable (es referencia inmutable en audit/logs).
        - Password NO se cambia desde acá — se usa ResetPasswordDialog.

Patrón calcado de TurnoFormDialog/DispositivoFormDialog: el diálogo
NO conoce el controller, recibe un callback ``on_submit(payload)``
que devuelve ``None`` (cierra) o ``str`` con el mensaje de error
(se muestra in-dialog y los campos se preservan).
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, TypedDict

import customtkinter as ctk

from core.models.rol import Rol


class UsuarioFormPayload(TypedDict, total=False):
    """Payload del form. ``username`` y ``password`` solo en modo alta."""

    username: str
    full_name: str
    role_id: int
    password: str
    password_confirm: str


SubmitFn = Callable[[UsuarioFormPayload], Optional[str]]


class UsuarioFormDialog(ctk.CTkToplevel):
    """Diálogo modal para crear o editar un usuario."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        roles_disponibles: List[Rol],
        modo_edicion: bool = False,
        initial_username: str = "",
        initial_full_name: str = "",
        initial_role_id: Optional[int] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre.
            title: "Nuevo usuario" / "Editar usuario — X".
            on_submit: Callback. None=OK cierra. str=mensaje error in-dialog.
            roles_disponibles: Lista de roles que el actor PUEDE asignar.
                Si el actor es ADMIN, esta lista NO contendrá SUPERADMIN
                (filtrado en el controller, defensa en profundidad).
            modo_edicion: ``True`` oculta username + password.
            initial_username: Solo si modo_edicion=True (read-only).
            initial_full_name: Precarga del campo.
            initial_role_id: Rol pre-seleccionado en el combo.
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._roles = roles_disponibles
        self._modo_edicion = modo_edicion
        self._log = logging.getLogger(self.__class__.__name__)

        self.title(title)
        self.geometry("480x440" if not modo_edicion else "480x290")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self._construir_ui(initial_username, initial_full_name, initial_role_id)
        self.after(10, self._configurar_modal)

    def _construir_ui(
        self,
        ini_username: str,
        ini_full_name: str,
        ini_role_id: Optional[int],
    ) -> None:
        self.grid_columnconfigure(1, weight=1)
        pad_x = 16
        pad_y = 6
        row = 0

        # Username (siempre visible; solo editable en alta).
        ctk.CTkLabel(self, text="Usuario:").grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=(pad_x, pad_y)
        )
        self._entry_username = ctk.CTkEntry(self, placeholder_text="3-32 chars: A-Z 0-9 . _ -")
        self._entry_username.insert(0, ini_username)
        if self._modo_edicion:
            self._entry_username.configure(state="disabled")
        self._entry_username.grid(
            row=row, column=1, sticky="ew", padx=(0, pad_x), pady=(pad_x, pad_y)
        )
        row += 1

        # Full name.
        ctk.CTkLabel(self, text="Nombre completo:").grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_full_name = ctk.CTkEntry(self, placeholder_text="Ej: Maria Lopez")
        self._entry_full_name.insert(0, ini_full_name)
        self._entry_full_name.grid(row=row, column=1, sticky="ew", padx=(0, pad_x), pady=pad_y)
        row += 1

        # Rol (combo).
        ctk.CTkLabel(self, text="Rol:").grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._labels_por_rol_id = {r.id: f"{r.name} ({r.code})" for r in self._roles}
        self._rol_id_por_label = {v: k for k, v in self._labels_por_rol_id.items()}
        valores = list(self._labels_por_rol_id.values())
        self._combo_rol = ctk.CTkComboBox(self, values=valores, state="readonly", width=320)
        seleccion_default = (
            self._labels_por_rol_id.get(ini_role_id, valores[0])
            if ini_role_id is not None and self._labels_por_rol_id
            else (valores[0] if valores else "")
        )
        self._combo_rol.set(seleccion_default)
        self._combo_rol.grid(row=row, column=1, sticky="ew", padx=(0, pad_x), pady=pad_y)
        row += 1

        # Password + confirmar (solo en alta).
        if not self._modo_edicion:
            ctk.CTkLabel(self, text="Contraseña:").grid(
                row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
            )
            self._entry_pwd = ctk.CTkEntry(self, show="*", placeholder_text="Mínimo 8 caracteres")
            self._entry_pwd.grid(row=row, column=1, sticky="ew", padx=(0, pad_x), pady=pad_y)
            row += 1

            ctk.CTkLabel(self, text="Confirmar:").grid(
                row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
            )
            self._entry_pwd_confirm = ctk.CTkEntry(
                self, show="*", placeholder_text="Repita la contraseña"
            )
            self._entry_pwd_confirm.grid(
                row=row, column=1, sticky="ew", padx=(0, pad_x), pady=pad_y
            )
            row += 1

        # Mensaje de error.
        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=440,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=pad_x, pady=(pad_y, 4)
        )
        row += 1

        # Botones.
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=row, column=0, columnspan=2, sticky="e", padx=pad_x, pady=(4, pad_x))
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
        if self._modo_edicion:
            self._entry_full_name.focus_set()
        else:
            self._entry_username.focus_set()

    def _configurar_modal(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            self._log.debug("grab_set falló (ventana destruida)")

    def _on_cancel(self) -> None:
        self.destroy()

    def _on_save(self) -> None:
        payload = self._recolectar_payload()
        if isinstance(payload, str):
            self._mostrar_error(payload)
            return
        result = self._on_submit(payload)
        if result is None:
            self.destroy()
        else:
            self._mostrar_error(result)

    def _recolectar_payload(self) -> UsuarioFormPayload | str:
        label_rol = self._combo_rol.get()
        role_id = self._rol_id_por_label.get(label_rol)
        if role_id is None:
            return "Seleccione un rol válido."

        full_name = self._entry_full_name.get().strip()
        if not full_name:
            return "El nombre completo es obligatorio."

        if self._modo_edicion:
            return UsuarioFormPayload(full_name=full_name, role_id=role_id)

        username = self._entry_username.get().strip()
        if not username:
            return "El nombre de usuario es obligatorio."
        password = self._entry_pwd.get()
        confirm = self._entry_pwd_confirm.get()
        if not password:
            return "La contraseña es obligatoria."
        if password != confirm:
            return "La contraseña y su confirmación no coinciden."

        return UsuarioFormPayload(
            username=username,
            full_name=full_name,
            role_id=role_id,
            password=password,
            password_confirm=confirm,
        )

    def _mostrar_error(self, mensaje: str) -> None:
        self._label_error.configure(text=mensaje)
