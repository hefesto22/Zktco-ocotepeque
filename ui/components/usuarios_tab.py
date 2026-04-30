"""Tab "Usuarios" del módulo Usuarios y roles (Sub-2.7a).

Diseño visual:

    ┌─────────────────────────────────────────────────────────┐
    │  [Buscar: ____________]  [☑ Ver inactivos]  [Refrescar] │
    ├─────────────────────────────────────────────────────────┤
    │  username — Nombre completo                              │
    │    Rol: Administrador · Estado: Activo                   │
    │    [Editar] [Resetear pass] [Desactivar] [Desbloquear]   │
    │  ...                                                      │
    ├─────────────────────────────────────────────────────────┤
    │                                  [+ Nuevo usuario]        │
    └─────────────────────────────────────────────────────────┘

Responsabilidad: solo render + interacción. Toda I/O via callables
inyectados desde la vista contenedora.
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Callable, List

import customtkinter as ctk

from core.models.rol import Rol
from core.services.errors import (
    CannotAssignSuperadminRoleError,
    CannotChangeSelfRoleError,
    CannotDeactivateOnlySuperadminError,
    CannotDeactivateSelfError,
    CannotResetSuperadminPasswordError,
    DuplicateUsernameError,
    InvalidUsernameError,
    UsuarioNotFoundError,
    WeakPasswordError,
)
from core.services.usuario_admin_service import UsuarioConRol
from ui.components.reset_password_dialog import ResetPasswordDialog
from ui.components.usuario_form_dialog import UsuarioFormDialog, UsuarioFormPayload

# Callables inyectados desde el controller.
ListUsuariosFn = Callable[[bool], List[UsuarioConRol]]
ListRolesFn = Callable[[], List[Rol]]
CreateFn = Callable[[str, str, int, str], None]
UpdateFn = Callable[[int, str, int], None]
ResetPwdFn = Callable[[int, str], None]
DeactivateFn = Callable[[int], None]
ReactivateFn = Callable[[int], None]
UnlockFn = Callable[[int], None]


class UsuariosTab(ctk.CTkFrame):
    """Tab de gestión de usuarios."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        list_usuarios_fn: ListUsuariosFn,
        list_roles_fn: ListRolesFn,
        create_fn: CreateFn,
        update_fn: UpdateFn,
        reset_password_fn: ResetPwdFn,
        deactivate_fn: DeactivateFn,
        reactivate_fn: ReactivateFn,
        unlock_fn: UnlockFn,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._list_usuarios_fn = list_usuarios_fn
        self._list_roles_fn = list_roles_fn
        self._create_fn = create_fn
        self._update_fn = update_fn
        self._reset_password_fn = reset_password_fn
        self._deactivate_fn = deactivate_fn
        self._reactivate_fn = reactivate_fn
        self._unlock_fn = unlock_fn
        self._log = logging.getLogger(self.__class__.__name__)

        self._busqueda_var = ctk.StringVar(value="")
        self._ver_inactivos_var = ctk.BooleanVar(value=True)
        self._cache_usuarios: List[UsuarioConRol] = []

        self._construir_ui()
        self._refrescar()

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(self) -> None:
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
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(0, weight=1)

        entry = ctk.CTkEntry(
            bar,
            textvariable=self._busqueda_var,
            placeholder_text="Buscar por usuario o nombre",
        )
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        entry.bind("<KeyRelease>", lambda _e: self._render_lista())

        ctk.CTkCheckBox(
            bar,
            text="Ver inactivos",
            variable=self._ver_inactivos_var,
            command=self._refrescar,
        ).grid(row=0, column=1, padx=(0, 8))

        ctk.CTkButton(bar, text="Refrescar", width=100, command=self._refrescar).grid(
            row=0, column=2, sticky="e"
        )
        return bar

    def _construir_form(self) -> ctk.CTkFrame:
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(form, text="+ Nuevo usuario", width=160, command=self._on_nuevo).grid(
            row=0, column=1, sticky="e"
        )
        return form

    # ── Render de la lista ────────────────────────────────────────────────

    def _refrescar(self) -> None:
        try:
            self._cache_usuarios = list(self._list_usuarios_fn(not self._ver_inactivos_var.get()))
        except Exception:  # noqa: BLE001
            self._log.exception("Error listando usuarios")
            messagebox.showerror("Error", "No se pudo cargar la lista de usuarios.")
            self._cache_usuarios = []
        self._render_lista()

    def _render_lista(self) -> None:
        for hijo in self._lista.winfo_children():
            hijo.destroy()
        filtro = self._busqueda_var.get().strip().lower()
        filtrados = (
            self._cache_usuarios
            if not filtro
            else [
                row
                for row in self._cache_usuarios
                if filtro in row.usuario.username.lower() or filtro in row.usuario.full_name.lower()
            ]
        )
        if not filtrados:
            ctk.CTkLabel(
                self._lista,
                text="No hay usuarios para mostrar.",
                text_color=("gray40", "gray60"),
                font=ctk.CTkFont(size=12, slant="italic"),
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
            return
        for idx, row in enumerate(filtrados):
            self._pintar_fila(idx, row)

    def _pintar_fila(self, idx: int, row: UsuarioConRol) -> None:
        u = row.usuario
        if u.id is None:
            return
        user_id = u.id

        fila = ctk.CTkFrame(self._lista, fg_color=("gray90", "gray20"))
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)

        # Estado: bloqueado tiene precedencia visual sobre inactivo.
        bloqueado = u.locked_until is not None
        if not u.is_active:
            estado_txt = "Inactivo"
            color_estado = ("gray45", "gray60")
        elif bloqueado:
            estado_txt = "Bloqueado"
            color_estado = ("#b71c1c", "#ef9a9a")
        else:
            estado_txt = "Activo"
            color_estado = ("#1b5e20", "#a5d6a7")

        texto = ctk.CTkFrame(fila, fg_color="transparent")
        texto.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        texto.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            texto,
            text=f"{u.username}  —  {u.full_name}",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            texto,
            text=f"Rol: {row.rol_name} ({row.rol_code})  ·  Estado: {estado_txt}",
            anchor="w",
            text_color=color_estado,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="ew")

        # Botones por fila.
        botones = ctk.CTkFrame(fila, fg_color="transparent")
        botones.grid(row=0, column=1, padx=8, pady=4)

        ctk.CTkButton(
            botones, text="Editar", width=90, command=lambda i=user_id: self._on_editar(i)
        ).grid(row=0, column=0, padx=2, pady=2)

        ctk.CTkButton(
            botones,
            text="Resetear pass",
            width=110,
            command=lambda i=user_id, n=u.username: self._on_reset_password(i, n),
        ).grid(row=0, column=1, padx=2, pady=2)

        if u.is_active:
            ctk.CTkButton(
                botones,
                text="Desactivar",
                width=100,
                fg_color=("gray65", "gray35"),
                hover_color=("gray50", "gray45"),
                command=lambda i=user_id, n=u.username: self._on_desactivar(i, n),
            ).grid(row=1, column=0, padx=2, pady=2)
        else:
            ctk.CTkButton(
                botones,
                text="Reactivar",
                width=100,
                command=lambda i=user_id: self._on_reactivar(i),
            ).grid(row=1, column=0, padx=2, pady=2)

        if bloqueado:
            ctk.CTkButton(
                botones,
                text="Desbloquear",
                width=110,
                command=lambda i=user_id: self._on_desbloquear(i),
            ).grid(row=1, column=1, padx=2, pady=2)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_nuevo(self) -> None:
        try:
            roles = self._list_roles_fn()
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando roles")
            messagebox.showerror("Error", "No se pudieron cargar los roles.")
            return

        def submit(p: UsuarioFormPayload) -> str | None:
            return self._intentar_crear(p)

        UsuarioFormDialog(
            self,
            title="Nuevo usuario",
            on_submit=submit,
            roles_disponibles=roles,
            modo_edicion=False,
        )

    def _on_editar(self, user_id: int) -> None:
        try:
            roles = self._list_roles_fn()
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando roles")
            messagebox.showerror("Error", "No se pudieron cargar los roles.")
            return
        actual = self._buscar_usuario(user_id)
        if actual is None:
            return

        def submit(p: UsuarioFormPayload) -> str | None:
            return self._intentar_actualizar(user_id, p)

        UsuarioFormDialog(
            self,
            title=f"Editar usuario — {actual.usuario.username}",
            on_submit=submit,
            roles_disponibles=roles,
            modo_edicion=True,
            initial_username=actual.usuario.username,
            initial_full_name=actual.usuario.full_name,
            initial_role_id=actual.usuario.role_id,
        )

    def _on_reset_password(self, user_id: int, username: str) -> None:
        def submit(new_pwd: str, _confirm: str) -> str | None:
            return self._intentar_reset(user_id, new_pwd)

        ResetPasswordDialog(self, username_objetivo=username, on_submit=submit)

    def _on_desactivar(self, user_id: int, username: str) -> None:
        confirmado = messagebox.askyesno(
            "Desactivar usuario",
            (
                f'¿Desactivar al usuario "{username}"?\n\n'
                "Su cuenta dejará de poder iniciar sesión, pero el "
                "historial se preserva. Puede reactivarlo cuando quiera."
            ),
        )
        if not confirmado:
            return
        try:
            self._deactivate_fn(user_id)
        except (
            CannotDeactivateSelfError,
            CannotDeactivateOnlySuperadminError,
            UsuarioNotFoundError,
        ) as exc:
            messagebox.showerror("No se puede desactivar", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error desactivando usuario id=%s", user_id)
            messagebox.showerror("Error", "No se pudo desactivar el usuario.")
            return
        self._refrescar()

    def _on_reactivar(self, user_id: int) -> None:
        try:
            self._reactivate_fn(user_id)
        except UsuarioNotFoundError as exc:
            messagebox.showerror("No encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando usuario id=%s", user_id)
            messagebox.showerror("Error", "No se pudo reactivar el usuario.")
            return
        self._refrescar()

    def _on_desbloquear(self, user_id: int) -> None:
        try:
            self._unlock_fn(user_id)
        except UsuarioNotFoundError as exc:
            messagebox.showerror("No encontrado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error desbloqueando usuario id=%s", user_id)
            messagebox.showerror("Error", "No se pudo desbloquear el usuario.")
            return
        self._refrescar()

    # ── Lógica de submit ──────────────────────────────────────────────────

    def _intentar_crear(self, p: UsuarioFormPayload) -> str | None:
        try:
            self._create_fn(
                p["username"],
                p["full_name"],
                p["role_id"],
                p["password"],
            )
        except (
            InvalidUsernameError,
            DuplicateUsernameError,
            WeakPasswordError,
            CannotAssignSuperadminRoleError,
            ValueError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado creando usuario")
            return "No se pudo crear el usuario. Intente de nuevo."
        self._refrescar()
        return None

    def _intentar_actualizar(self, user_id: int, p: UsuarioFormPayload) -> str | None:
        try:
            self._update_fn(user_id, p["full_name"], p["role_id"])
        except (
            CannotAssignSuperadminRoleError,
            CannotChangeSelfRoleError,
            UsuarioNotFoundError,
            ValueError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado actualizando usuario id=%s", user_id)
            return "No se pudo actualizar el usuario. Intente de nuevo."
        self._refrescar()
        return None

    def _intentar_reset(self, user_id: int, new_pwd: str) -> str | None:
        try:
            self._reset_password_fn(user_id, new_pwd)
        except (
            WeakPasswordError,
            CannotResetSuperadminPasswordError,
            UsuarioNotFoundError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error inesperado reseteando password id=%s", user_id)
            return "No se pudo cambiar la contraseña. Intente de nuevo."
        return None

    def _buscar_usuario(self, user_id: int) -> UsuarioConRol | None:
        for row in self._cache_usuarios:
            if row.usuario.id == user_id:
                return row
        return None
