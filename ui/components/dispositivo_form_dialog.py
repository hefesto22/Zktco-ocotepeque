"""Diálogo modal ``DispositivoFormDialog`` (Plan B / Sub-2.4b).

Formulario reutilizable para crear y editar dispositivos ZKTeco. Sirve
el mismo diálogo tanto para "Nuevo dispositivo" (valores default) como
para "Editar dispositivo X" (valores precargados desde un
``Dispositivo`` existente).

Diseño del API (calcado del ``TurnoFormDialog``):

    El diálogo NO llama directamente al controller. Recibe un callback
    ``on_submit(payload)`` que el caller usa para invocar el service:

        - Si el callback devuelve ``None`` → operación exitosa, el
          diálogo se cierra.
        - Si devuelve un ``str`` → se muestra como mensaje de error en
          rojo dentro del diálogo, los campos se preservan, el usuario
          corrige y reintenta.

    Este pattern permite que ``InvalidIPError``, ``InvalidPuertoError``,
    ``DuplicateDispositivoNombreError`` y
    ``DuplicateDispositivoEndpointError`` se muestren in-dialog sin
    cerrar la ventana.

Validación local (puerto entero) — el resto (IP válida, duplicados,
nombre vacío) lo decide el service. Esto evita duplicar reglas de
negocio en la UI.

Diseño visual (approx 460x260 px):

    ┌─ Nuevo dispositivo ───────────────────────┐
    │  Nombre:   [_______________________]       │
    │  IP:       [___.___.___.___]               │
    │  Puerto:   [_____]    (default 4370)       │
    │                                            │
    │  (mensaje de error en rojo, si aplica)     │
    │                                            │
    │                     [Cancelar]  [Guardar]  │
    └────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, TypedDict

import customtkinter as ctk

from core.models.dispositivo import PUERTO_ZKTECO_DEFAULT


class DispositivoFormPayload(TypedDict):
    """Resultado del form al pulsar Guardar."""

    nombre: str
    ip: str
    puerto: int


# Callback del caller: recibe el payload y decide si cerrar o mostrar error.
# Devuelve ``None`` en éxito (cierra diálogo) o ``str`` con el mensaje.
SubmitFn = Callable[[DispositivoFormPayload], Optional[str]]


class DispositivoFormDialog(ctk.CTkToplevel):
    """Diálogo modal para crear o editar un dispositivo ZKTeco."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        initial_nombre: str = "",
        initial_ip: str = "",
        initial_puerto: int = PUERTO_ZKTECO_DEFAULT,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre — el diálogo se vuelve transient suyo.
            title: Texto de la barra de título (ej. "Nuevo dispositivo").
            on_submit: Callback que el caller implementa para invocar el
                controller. ``None`` si OK (cierra), ``str`` con el
                mensaje a mostrar si falla.
            initial_nombre: Valor precargado del nombre. Vacío = nuevo.
            initial_ip: IPv4 precargada (ej. "192.168.0.101").
            initial_puerto: Puerto TCP. Default 4370 (estándar ZKTeco).
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._log = logging.getLogger(self.__class__.__name__)

        self.title(title)
        self.geometry("460x270")
        self.resizable(False, False)
        # Transient al parent: minimizar el parent minimiza éste.
        self.transient(parent.winfo_toplevel())

        self._construir_ui(initial_nombre, initial_ip, initial_puerto)

        # grab_set tiene que correr después de que la ventana sea visible.
        self.after(10, self._configurar_modal)

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(
        self,
        ini_nombre: str,
        ini_ip: str,
        ini_puerto: int,
    ) -> None:
        """Arma el formulario completo."""
        self.grid_columnconfigure(1, weight=1)

        pad_x = 16
        pad_y = 6

        # Fila 0: Nombre.
        ctk.CTkLabel(self, text="Nombre:").grid(
            row=0, column=0, sticky="e", padx=(pad_x, 8), pady=(pad_x, pad_y)
        )
        self._entry_nombre = ctk.CTkEntry(self, placeholder_text="Ej: Sede Principal")
        self._entry_nombre.insert(0, ini_nombre)
        self._entry_nombre.grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(0, pad_x), pady=(pad_x, pad_y)
        )

        # Fila 1: IP.
        ctk.CTkLabel(self, text="IP:").grid(
            row=1, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_ip = ctk.CTkEntry(self, placeholder_text="192.168.0.101")
        self._entry_ip.insert(0, ini_ip)
        self._entry_ip.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, pad_x), pady=pad_y)

        # Fila 2: Puerto.
        ctk.CTkLabel(self, text="Puerto:").grid(
            row=2, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        self._entry_puerto = ctk.CTkEntry(self, placeholder_text="4370", width=100)
        self._entry_puerto.insert(0, str(ini_puerto))
        self._entry_puerto.grid(row=2, column=1, sticky="w", pady=pad_y)

        ctk.CTkLabel(
            self,
            text="(default 4370)",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=2, sticky="w", padx=(8, pad_x), pady=pad_y)

        # Fila 3: Mensaje de error (inicialmente oculto).
        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=420,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=3, column=0, columnspan=4, sticky="ew", padx=pad_x, pady=(pad_y, 4)
        )

        # Fila 4: Botones.
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=4, column=0, columnspan=4, sticky="e", padx=pad_x, pady=(4, pad_x))

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
        for entry in (self._entry_nombre, self._entry_ip, self._entry_puerto):
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

    def _recolectar_payload(self) -> DispositivoFormPayload | str:
        """Lee los widgets y devuelve el payload o un mensaje de error.

        Solo valida la conversión de tipo del puerto (a entero). El
        resto de reglas — nombre vacío, formato IP, duplicados — las
        impone el service vía ``on_submit``. No duplicamos validación
        en la UI.
        """
        puerto_str = self._entry_puerto.get().strip()
        try:
            puerto = int(puerto_str)
        except ValueError:
            return "Puerto debe ser un número entero (1–65535)."

        return DispositivoFormPayload(
            nombre=self._entry_nombre.get().strip(),
            ip=self._entry_ip.get().strip(),
            puerto=puerto,
        )

    def _mostrar_error(self, mensaje: str) -> None:
        """Muestra el mensaje de error debajo del form."""
        self._label_error.configure(text=mensaje)
