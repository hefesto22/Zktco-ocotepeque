"""Diálogo modal ``EmpleadoFormDialog``.

Formulario reutilizable para crear y editar empleados. Como en
``TurnoFormDialog`` (Sub-2.5), el diálogo NO llama al controller: recibe
un callback ``on_submit(payload)`` que devuelve ``None`` si todo OK
(se cierra) o un ``str`` con el mensaje a mostrar in-dialog.

Modos:

    El mismo diálogo sirve para "Nuevo empleado" y "Editar empleado".
    Controlado por ``modo_alta``:

    * ``modo_alta=True``  — muestra la sección "Turno inicial" con
      combo de turnos y fecha de inicio. El payload incluye
      ``turno_id`` y ``fecha_inicio_turno``.
    * ``modo_alta=False`` — oculta la sección de turno. El payload
      solo trae los campos editables del empleado. Los combos de
      departamento y cargo se precargan con los valores actuales.

Pattern de combos:

    customtkinter no permite asociar un valor arbitrario a cada ítem
    de un ``CTkOptionMenu``. Para poder trabajar con ``id`` numéricos
    sin perder robustez ante nombres duplicados o renombres, el diálogo
    recibe listas de tuplas ``(id, label)`` y mantiene un ``dict``
    interno ``label -> id`` para traducir al guardar.

Diseño visual (aprox 520x620 px):

    ┌─ Nuevo empleado ───────────────────────────┐
    │  DNI:            [XXXX-XXXX-XXXXX]         │
    │  Nombres:        [___________________]     │
    │  Apellidos:      [___________________]     │
    │  Departamento:   [combo ▾]                 │
    │  Cargo:          [combo ▾]                 │
    │  Fecha ingreso:  [YYYY-MM-DD]              │
    │  Teléfono:       [___________________]     │
    │  Email:          [___________________]     │
    │  ZKTeco ID:      [_____]                   │
    │                                            │
    │  ── Turno inicial (solo en alta) ──        │
    │  Turno:          [combo ▾]                 │
    │  Fecha inicio:   [YYYY-MM-DD]              │
    │                                            │
    │  (mensaje de error en rojo, si aplica)     │
    │                                            │
    │                    [Cancelar]  [Guardar]   │
    └────────────────────────────────────────────┘
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Callable, Dict, List, NotRequired, Optional, Tuple, TypedDict, Union

import customtkinter as ctk


class EmpleadoFormPayload(TypedDict):
    """Resultado del form al pulsar Guardar.

    Los primeros 9 campos están siempre presentes. ``turno_id`` y
    ``fecha_inicio_turno`` solo aparecen cuando el diálogo se abre en
    modo alta (``modo_alta=True``).
    """

    dni: str
    nombres: str
    apellidos: str
    departamento_id: int
    cargo_id: int
    fecha_ingreso: str
    telefono: Optional[str]
    email: Optional[str]
    zkteco_id: Optional[int]
    turno_id: NotRequired[int]
    fecha_inicio_turno: NotRequired[str]


# Callback del caller: recibe el payload y decide si cerrar o mostrar error.
SubmitFn = Callable[[EmpleadoFormPayload], Optional[str]]

# Opción de combo: (id en BD, label visible al usuario).
ComboOption = Tuple[int, str]


def _hoy_iso() -> str:
    """Fecha de hoy en formato ``YYYY-MM-DD`` (zona local del equipo)."""
    return _dt.date.today().isoformat()


class EmpleadoFormDialog(ctk.CTkToplevel):
    """Diálogo modal para crear o editar un empleado."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        on_submit: SubmitFn,
        departamentos: List[ComboOption],
        cargos: List[ComboOption],
        turnos: Optional[List[ComboOption]] = None,
        modo_alta: bool = True,
        initial_dni: str = "",
        initial_nombres: str = "",
        initial_apellidos: str = "",
        initial_departamento_id: Optional[int] = None,
        initial_cargo_id: Optional[int] = None,
        initial_fecha_ingreso: Optional[str] = None,
        initial_telefono: str = "",
        initial_email: str = "",
        initial_zkteco_id: Optional[int] = None,
        cargos_por_depto_provider: Optional[Callable[[int], List[ComboOption]]] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre; el diálogo queda transient al toplevel.
            title: Texto de la barra de título.
            on_submit: Callback del caller. ``None`` = OK (cierra);
                ``str`` = error a mostrar (no cierra).
            departamentos: Lista de ``(id, nombre)`` para el combo.
                Debe venir pre-filtrada por ``is_active`` por el caller.
            cargos: Lista de ``(id, nombre)`` para el combo. Pre-filtrada.
            turnos: Lista de ``(id, nombre)`` para el combo de turno
                inicial. Obligatoria si ``modo_alta=True``; ignorada si
                ``modo_alta=False``.
            modo_alta: ``True`` muestra la sección "Turno inicial" y
                exige turno + fecha de inicio en el payload.
            initial_*: Valores precargados (modo edición). En alta se
                dejan en sus defaults.

        Raises:
            ValueError: Si ``modo_alta=True`` y ``turnos`` no se pasó,
                o si las listas de catálogos vienen vacías.
        """
        super().__init__(parent)
        self._on_submit = on_submit
        self._modo_alta = modo_alta
        self._log = logging.getLogger(self.__class__.__name__)

        if not departamentos:
            raise ValueError("Se requiere al menos un departamento activo.")
        if not cargos:
            raise ValueError("Se requiere al menos un cargo activo.")
        if modo_alta and not turnos:
            raise ValueError("En modo alta se requiere al menos un turno activo.")

        self.title(title)
        # Altura depende del modo (alta lleva 2 filas extra).
        alto = 620 if modo_alta else 520
        self.geometry(f"540x{alto}")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        # Los mapas label -> id permiten recuperar el id sin importar
        # si el usuario cambió el texto (no debería, los combos son
        # OptionMenu, pero mantenemos la defensiva).
        self._dep_map: Dict[str, int] = {label: id_ for id_, label in departamentos}
        self._car_map: Dict[str, int] = {label: id_ for id_, label in cargos}
        # Sub-3.2.A: provider opcional que devuelve los cargos elegibles
        # para un departamento (globales + específicos). Cuando se pasa,
        # el dropdown de cargo se recarga al cambiar el de departamento.
        self._cargos_por_depto_provider = cargos_por_depto_provider
        self._tur_map: Dict[str, int] = (
            {label: id_ for id_, label in (turnos or [])} if modo_alta else {}
        )

        self._construir_ui(
            departamentos=departamentos,
            cargos=cargos,
            turnos=list(turnos) if (modo_alta and turnos) else [],
            initial_dni=initial_dni,
            initial_nombres=initial_nombres,
            initial_apellidos=initial_apellidos,
            initial_departamento_id=initial_departamento_id,
            initial_cargo_id=initial_cargo_id,
            initial_fecha_ingreso=initial_fecha_ingreso or _hoy_iso(),
            initial_telefono=initial_telefono,
            initial_email=initial_email,
            initial_zkteco_id=initial_zkteco_id,
        )

        self.after(10, self._configurar_modal)

    # ── Construcción del árbol ────────────────────────────────────────────

    def _construir_ui(
        self,
        departamentos: List[ComboOption],
        cargos: List[ComboOption],
        turnos: List[ComboOption],
        initial_dni: str,
        initial_nombres: str,
        initial_apellidos: str,
        initial_departamento_id: Optional[int],
        initial_cargo_id: Optional[int],
        initial_fecha_ingreso: str,
        initial_telefono: str,
        initial_email: str,
        initial_zkteco_id: Optional[int],
    ) -> None:
        """Arma el formulario completo."""
        self.grid_columnconfigure(1, weight=1)
        pad_x = 16
        pad_y = 4

        row = 0

        # DNI
        self._entry_dni = self._fila_entry(
            row=row,
            label="DNI:",
            placeholder="XXXX-XXXX-XXXXX",
            initial=initial_dni,
            pad_x=pad_x,
            pad_y=pad_y,
            pad_top=pad_x,
        )
        row += 1

        # Nombres
        self._entry_nombres = self._fila_entry(
            row=row,
            label="Nombres:",
            placeholder="Ej: Juan Carlos",
            initial=initial_nombres,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        row += 1

        # Apellidos
        self._entry_apellidos = self._fila_entry(
            row=row,
            label="Apellidos:",
            placeholder="Ej: Pérez López",
            initial=initial_apellidos,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        row += 1

        # Departamento (combo)
        self._combo_dep = self._fila_combo(
            row=row,
            label="Departamento:",
            opciones=departamentos,
            initial_id=initial_departamento_id,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        # Sub-3.2.A: cuando el operador cambia el dropdown de
        # departamento, refrescamos el de cargos con la lista filtrada.
        if self._cargos_por_depto_provider is not None:
            self._combo_dep.configure(command=self._on_departamento_change)
        row += 1

        # Cargo (combo)
        self._combo_cargo = self._fila_combo(
            row=row,
            label="Cargo:",
            opciones=cargos,
            initial_id=initial_cargo_id,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        row += 1

        # Fecha ingreso
        self._entry_fecha_ingreso = self._fila_entry(
            row=row,
            label="Fecha ingreso:",
            placeholder="YYYY-MM-DD",
            initial=initial_fecha_ingreso,
            pad_x=pad_x,
            pad_y=pad_y,
            ancho=140,
        )
        row += 1

        # Teléfono
        self._entry_telefono = self._fila_entry(
            row=row,
            label="Teléfono:",
            placeholder="(opcional)",
            initial=initial_telefono,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        row += 1

        # Email
        self._entry_email = self._fila_entry(
            row=row,
            label="Email:",
            placeholder="(opcional)",
            initial=initial_email,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        row += 1

        # ZKTeco ID
        zkteco_str = "" if initial_zkteco_id is None else str(initial_zkteco_id)
        self._entry_zkteco = self._fila_entry(
            row=row,
            label="ZKTeco ID:",
            placeholder="(opcional)",
            initial=zkteco_str,
            pad_x=pad_x,
            pad_y=pad_y,
            ancho=120,
        )
        row += 1

        # Sección Turno inicial (solo en alta).
        self._combo_turno: Optional[ctk.CTkOptionMenu] = None
        self._entry_fecha_inicio_turno: Optional[ctk.CTkEntry] = None
        if self._modo_alta:
            # Separador visual.
            sep = ctk.CTkLabel(
                self,
                text="── Turno inicial ──",
                text_color=("gray40", "gray65"),
                anchor="w",
            )
            sep.grid(
                row=row,
                column=0,
                columnspan=4,
                sticky="ew",
                padx=pad_x,
                pady=(pad_y * 2, pad_y),
            )
            row += 1

            self._combo_turno = self._fila_combo(
                row=row,
                label="Turno:",
                opciones=turnos,
                initial_id=None,
                pad_x=pad_x,
                pad_y=pad_y,
            )
            row += 1

            self._entry_fecha_inicio_turno = self._fila_entry(
                row=row,
                label="Fecha inicio:",
                placeholder="YYYY-MM-DD",
                initial=_hoy_iso(),
                pad_x=pad_x,
                pad_y=pad_y,
                ancho=140,
            )
            row += 1

        # Mensaje de error (inicialmente oculto).
        self._label_error = ctk.CTkLabel(
            self,
            text="",
            text_color=("red", "#ff6b6b"),
            wraplength=480,
            justify="left",
            anchor="w",
        )
        self._label_error.grid(
            row=row, column=0, columnspan=4, sticky="ew", padx=pad_x, pady=(pad_y * 2, 4)
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

        btn_save = ctk.CTkButton(botones, text="Guardar", width=100, command=self._on_save)
        btn_save.grid(row=0, column=1)

        # Enter dispara Guardar desde cualquier entry.
        entries: List[ctk.CTkEntry] = [
            self._entry_dni,
            self._entry_nombres,
            self._entry_apellidos,
            self._entry_fecha_ingreso,
            self._entry_telefono,
            self._entry_email,
            self._entry_zkteco,
        ]
        if self._entry_fecha_inicio_turno is not None:
            entries.append(self._entry_fecha_inicio_turno)
        for entry in entries:
            entry.bind("<Return>", lambda _e: self._on_save())

        # Esc cierra sin guardar.
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self._entry_dni.focus_set()

    # ── Helpers de construcción ───────────────────────────────────────────

    def _fila_entry(
        self,
        row: int,
        label: str,
        placeholder: str,
        initial: str,
        pad_x: int,
        pad_y: int,
        pad_top: Optional[int] = None,
        ancho: Optional[int] = None,
    ) -> ctk.CTkEntry:
        """Crea una fila ``[Label:] [Entry]`` y devuelve el entry."""
        top = pad_top if pad_top is not None else pad_y
        ctk.CTkLabel(self, text=label).grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=(top, pad_y)
        )
        entry = ctk.CTkEntry(
            self, placeholder_text=placeholder, width=ancho if ancho is not None else 0
        )
        if initial:
            entry.insert(0, initial)
        if ancho is None:
            entry.grid(
                row=row,
                column=1,
                columnspan=3,
                sticky="ew",
                padx=(0, pad_x),
                pady=(top, pad_y),
            )
        else:
            entry.grid(row=row, column=1, sticky="w", padx=(0, pad_x), pady=(top, pad_y))
        return entry

    def _fila_combo(
        self,
        row: int,
        label: str,
        opciones: List[ComboOption],
        initial_id: Optional[int],
        pad_x: int,
        pad_y: int,
    ) -> ctk.CTkOptionMenu:
        """Crea una fila ``[Label:] [OptionMenu]`` y devuelve el combo."""
        ctk.CTkLabel(self, text=label).grid(
            row=row, column=0, sticky="e", padx=(pad_x, 8), pady=pad_y
        )
        labels = [lbl for _id, lbl in opciones]
        combo = ctk.CTkOptionMenu(self, values=labels)
        # Precarga: si se pidió un id inicial y existe en las opciones,
        # lo seleccionamos; si no, dejamos el primero.
        if initial_id is not None:
            for id_, lbl in opciones:
                if id_ == initial_id:
                    combo.set(lbl)
                    break
        combo.grid(
            row=row,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, pad_x),
            pady=pad_y,
        )
        return combo

    def _configurar_modal(self) -> None:
        """Activa el grab modal una vez la ventana es visible."""
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 — si el widget ya fue destruido
            self._log.debug("grab_set falló (ventana destruida)")

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_departamento_change(self, _selected_label: str) -> None:
        """Sub-3.2.A: refresca el dropdown de cargos al cambiar el de depto.

        Pide al provider la lista de cargos elegibles (globales +
        específicos del depto seleccionado) y reemplaza las opciones
        del combo. Si el cargo previamente seleccionado sigue siendo
        elegible, se preserva; si no, se posiciona en la primera opción.
        """
        if self._cargos_por_depto_provider is None:
            return
        dep_label = self._combo_dep.get()
        dep_id = self._dep_map.get(dep_label)
        if dep_id is None:
            return
        try:
            nuevas_opciones = self._cargos_por_depto_provider(dep_id)
        except Exception:  # noqa: BLE001
            self._log.exception("No se pudieron recargar los cargos.")
            return
        if not nuevas_opciones:
            # Sin cargos elegibles — dejamos el combo en blanco para que
            # el operador note el problema y cree un cargo apropiado.
            self._car_map = {}
            self._combo_cargo.configure(values=[""])
            self._combo_cargo.set("")
            return
        labels_nuevos = [lbl for _id, lbl in nuevas_opciones]
        actual = self._combo_cargo.get()
        self._car_map = {label: id_ for id_, label in nuevas_opciones}
        self._combo_cargo.configure(values=labels_nuevos)
        if actual in labels_nuevos:
            self._combo_cargo.set(actual)
        else:
            self._combo_cargo.set(labels_nuevos[0])

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

    def _recolectar_payload(self) -> Union[EmpleadoFormPayload, str]:
        """Lee widgets y devuelve el payload o un mensaje de error UI.

        Solo se validan conversiones de tipo y selección en combos.
        Las reglas de dominio (formato DNI, fecha existente, duplicados)
        las impone el service — no duplicamos validación.
        """
        dep_label = self._combo_dep.get()
        car_label = self._combo_cargo.get()
        if dep_label not in self._dep_map:
            return "Seleccione un departamento válido."
        if car_label not in self._car_map:
            return "Seleccione un cargo válido."

        zkteco_raw = self._entry_zkteco.get().strip()
        zkteco_id: Optional[int] = None
        if zkteco_raw:
            try:
                zkteco_id = int(zkteco_raw)
            except ValueError:
                return "ZKTeco ID debe ser un número entero."
            if zkteco_id <= 0:
                return "ZKTeco ID debe ser un número positivo."

        telefono_raw = self._entry_telefono.get().strip()
        email_raw = self._entry_email.get().strip()

        payload: EmpleadoFormPayload = {
            "dni": self._entry_dni.get().strip(),
            "nombres": self._entry_nombres.get().strip(),
            "apellidos": self._entry_apellidos.get().strip(),
            "departamento_id": self._dep_map[dep_label],
            "cargo_id": self._car_map[car_label],
            "fecha_ingreso": self._entry_fecha_ingreso.get().strip(),
            "telefono": telefono_raw or None,
            "email": email_raw or None,
            "zkteco_id": zkteco_id,
        }

        if self._modo_alta:
            assert self._combo_turno is not None
            assert self._entry_fecha_inicio_turno is not None
            tur_label = self._combo_turno.get()
            if tur_label not in self._tur_map:
                return "Seleccione un turno inicial válido."
            payload["turno_id"] = self._tur_map[tur_label]
            payload["fecha_inicio_turno"] = self._entry_fecha_inicio_turno.get().strip()

        return payload

    def _mostrar_error(self, mensaje: str) -> None:
        """Muestra el mensaje de error debajo del form."""
        self._label_error.configure(text=mensaje)
