"""``GestionTurnosDialog`` — UI multi-turnos por empleado (Sub-3.3).

Reemplaza al antiguo "Asignar/Cambiar turno" para empleados que necesitan
varios turnos vigentes en paralelo (caso real: lun-vie 8-17 + sábado
8-13). Muestra:

    - La lista de asignaciones vigentes del empleado, con el detalle del
      turno aplicado y los días que cubre.
    - Un botón **Cerrar** por cada vigente (cierra solo esa asignación).
    - Un botón **+ Agregar turno** para sumar uno nuevo paralelo. La
      validación de "bitmask no solapado" la hace el service — si los
      días chocan, se muestra el error en pantalla.

Diseño visual:

    ┌─ Gestionar turnos — Pérez, Juan ─────────────────────────────────┐
    │  Vigentes:                                                       │
    │  ─────────────────────────────────────────────────────────────── │
    │  Test Diurno 08-17  ·  Lun-Vie  ·  desde 2026-01-01   [Cerrar]   │
    │  Sábado 08-13       ·  Sáb      ·  desde 2026-01-15   [Cerrar]   │
    │                                                                  │
    │                                                  [+ Agregar turno]│
    │                                                                  │
    │  (mensaje de error en rojo, si aplica)                           │
    │                                                                  │
    │                                                       [Cerrar ⨯] │
    └──────────────────────────────────────────────────────────────────┘

El diálogo NO llama al controller directamente — recibe callables
explícitos por inyección, igual que el resto de los componentes UI.
"""

from __future__ import annotations

import datetime as _dt
import logging
from tkinter import messagebox
from typing import Callable, List, Optional, Tuple

import customtkinter as ctk

from core.models.empleado import Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.models.turno import Turno

# Etiquetas cortas por bit (LUN, MAR, MIE, JUE, VIE, SAB, DOM).
_BITS_DIAS: Tuple[Tuple[int, str], ...] = (
    (1 << 6, "Lun"),
    (1 << 5, "Mar"),
    (1 << 4, "Mié"),
    (1 << 3, "Jue"),
    (1 << 2, "Vie"),
    (1 << 1, "Sáb"),
    (1 << 0, "Dom"),
)


def _bitmask_a_etiqueta(bitmask: int) -> str:
    """Devuelve una etiqueta corta tipo ``"Lun-Vie"`` o ``"Sáb"``."""
    activos = [label for bit, label in _BITS_DIAS if bitmask & bit]
    if not activos:
        return "(sin días)"
    # Detectar rango contiguo lun-vie para abreviarlo.
    if activos == ["Lun", "Mar", "Mié", "Jue", "Vie"]:
        return "Lun-Vie"
    if activos == [
        "Lun",
        "Mar",
        "Mié",
        "Jue",
        "Vie",
        "Sáb",
        "Dom",
    ]:
        return "Toda la semana"
    return ", ".join(activos)


def _hoy_iso() -> str:
    """Fecha de hoy en formato ``YYYY-MM-DD``."""
    return _dt.date.today().isoformat()


class GestionTurnosDialog(ctk.CTkToplevel):
    """Diálogo para listar, agregar y cerrar turnos vigentes (Sub-3.3)."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        empleado: Empleado,
        list_vigentes_fn: Callable[[int], List[EmpleadoTurno]],
        list_turnos_fn: Callable[..., List[Turno]],
        asignar_fn: Callable[[int, int, str], None],
        cerrar_fn: Callable[[int, str], None],
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Construye el diálogo.

        Args:
            parent: Widget padre; el diálogo queda transient al toplevel.
            empleado: Empleado cuyos turnos se gestionan.
            list_vigentes_fn: ``controller.list_turnos_vigentes(emp_id)``.
            list_turnos_fn: ``controller.list_turnos(solo_activos=True)``
                — para poblar el dropdown de "Agregar".
            asignar_fn:
                ``controller.asignar_turno(emp_id, turno_id, fecha_inicio)``.
            cerrar_fn:
                ``controller.cerrar_asignacion(asignacion_id, fecha_fin)``.
            on_done: callback que se invoca cada vez que se confirma un
                cambio (alta o cierre). Útil para que la lista del
                empleado refresque el contador de turnos vigentes.
        """
        super().__init__(parent)
        assert empleado.id is not None
        self._empleado = empleado
        self._list_vigentes = list_vigentes_fn
        self._list_turnos = list_turnos_fn
        self._asignar = asignar_fn
        self._cerrar = cerrar_fn
        self._on_done = on_done
        self._log = logging.getLogger(self.__class__.__name__)

        self._turnos_por_id: dict[int, Turno] = {}

        self.title(f"Gestionar turnos — {empleado.nombres} {empleado.apellidos}")
        self.geometry("640x460")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self._construir_ui()
        self._refrescar()
        self.after(10, self._configurar_modal)

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Empleado: {self._empleado.nombres} {self._empleado.apellidos}",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))

        ctk.CTkLabel(self, text="Asignaciones vigentes:", anchor="w").grid(
            row=1, column=0, sticky="ew", padx=16, pady=(4, 4)
        )

        self._lista = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._lista.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self._lista.grid_columnconfigure(0, weight=1)

        botones_top = ctk.CTkFrame(self, fg_color="transparent")
        botones_top.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        botones_top.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            botones_top,
            text="+ Agregar turno",
            width=160,
            command=self._on_agregar,
        ).grid(row=0, column=1, sticky="e")

        self._error_label = ctk.CTkLabel(self, text="", text_color=("#a00", "#f88"))
        self._error_label.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 4))

        botones_btm = ctk.CTkFrame(self, fg_color="transparent")
        botones_btm.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 16))
        botones_btm.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(botones_btm, text="Cerrar", width=120, command=self.destroy).grid(
            row=0, column=1, sticky="e"
        )

    def _configurar_modal(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refrescar(self) -> None:
        """Recarga la lista de vigentes + el catálogo de turnos."""
        try:
            assert self._empleado.id is not None
            vigentes = self._list_vigentes(self._empleado.id)
            turnos_activos = self._list_turnos(solo_activos=True)
        except Exception as exc:  # noqa: BLE001
            self._log.exception("Error refrescando GestionTurnosDialog")
            self._mostrar_error(f"No se pudieron cargar los turnos: {exc}")
            return

        self._turnos_por_id = {t.id: t for t in turnos_activos if t.id is not None}

        for child in self._lista.winfo_children():
            child.destroy()

        if not vigentes:
            ctk.CTkLabel(
                self._lista,
                text="(El empleado no tiene asignaciones vigentes)",
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=12)
            return

        for fila_idx, asignacion in enumerate(vigentes):
            self._render_fila(asignacion, fila_idx)

    def _render_fila(self, asignacion: EmpleadoTurno, fila_idx: int) -> None:
        turno = self._turnos_por_id.get(asignacion.turno_id)
        if turno is None:
            etiqueta = (
                f"(turno_id={asignacion.turno_id} no disponible)  ·  "
                f"desde {asignacion.fecha_inicio}"
            )
        else:
            dias = _bitmask_a_etiqueta(turno.dias_semana)
            etiqueta = (
                f"{turno.nombre}  ·  {turno.hora_entrada}-{turno.hora_salida}"
                f"  ·  {dias}  ·  desde {asignacion.fecha_inicio}"
            )

        fila = ctk.CTkFrame(self._lista, fg_color="transparent")
        fila.grid(row=fila_idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(fila, text=etiqueta, anchor="w").grid(row=0, column=0, sticky="ew", padx=8)
        ctk.CTkButton(
            fila,
            text="Cerrar",
            width=90,
            fg_color="#a85050",
            hover_color="#874242",
            command=lambda a=asignacion: self._on_cerrar(a),
        ).grid(row=0, column=1, padx=4)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_cerrar(self, asignacion: EmpleadoTurno) -> None:
        assert asignacion.id is not None
        if not messagebox.askyesno(
            "Confirmar cierre",
            "¿Cerrar esta asignación de turno? La acción queda registrada en\n"
            "el audit_log y deja la asignación con fecha_fin = hoy.",
        ):
            return
        try:
            self._cerrar(asignacion.id, _hoy_iso())
        except Exception as exc:  # noqa: BLE001
            self._mostrar_error(f"No se pudo cerrar: {exc}")
            return
        self._mostrar_error("")
        self._notificar_done()
        self._refrescar()

    def _on_agregar(self) -> None:
        """Abre un mini-diálogo para elegir turno + fecha de inicio."""
        try:
            turnos_activos = self._list_turnos(solo_activos=True)
        except Exception as exc:  # noqa: BLE001
            self._mostrar_error(f"No se pudieron cargar los turnos: {exc}")
            return
        if not turnos_activos:
            self._mostrar_error("No hay turnos activos. Crealos en Turnos antes de asignar.")
            return

        opciones = [(t.id, t.nombre) for t in turnos_activos if t.id is not None]
        AgregarTurnoDialog(
            self,
            opciones=opciones,
            asignar_fn=self._invocar_asignar,
        )

    def _invocar_asignar(self, turno_id: int, fecha_inicio: str) -> Optional[str]:
        """Callback del sub-diálogo. Devuelve None si OK; str con error si no."""
        assert self._empleado.id is not None
        try:
            self._asignar(self._empleado.id, turno_id, fecha_inicio)
        except Exception as exc:  # noqa: BLE001
            return str(exc)
        self._mostrar_error("")
        self._notificar_done()
        self._refrescar()
        return None

    def _mostrar_error(self, mensaje: str) -> None:
        self._error_label.configure(text=mensaje)

    def _notificar_done(self) -> None:
        if self._on_done is not None:
            try:
                self._on_done()
            except Exception:  # noqa: BLE001
                self._log.exception("on_done callback falló (silenciado)")


class AgregarTurnoDialog(ctk.CTkToplevel):
    """Sub-diálogo para elegir turno + fecha de inicio."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        opciones: List[Tuple[int, str]],
        asignar_fn: Callable[[int, str], Optional[str]],
    ) -> None:
        super().__init__(parent)
        self._asignar = asignar_fn
        self._opciones_map = {label: id_ for id_, label in opciones}

        self.title("Agregar turno paralelo")
        self.geometry("420x230")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Turno:").grid(
            row=0, column=0, padx=(16, 8), pady=(20, 6), sticky="e"
        )
        self._turno_var = ctk.StringVar(value=opciones[0][1])
        ctk.CTkOptionMenu(
            self,
            values=[lbl for _id, lbl in opciones],
            variable=self._turno_var,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(20, 6))

        ctk.CTkLabel(self, text="Desde:").grid(row=1, column=0, padx=(16, 8), pady=6, sticky="e")
        self._fecha_var = ctk.StringVar(value=_hoy_iso())
        ctk.CTkEntry(self, textvariable=self._fecha_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 16), pady=6
        )

        self._error_label = ctk.CTkLabel(self, text="", text_color=("#a00", "#f88"))
        self._error_label.grid(row=2, column=0, columnspan=2, padx=16, pady=(4, 0))

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=3, column=0, columnspan=2, pady=(16, 16))
        ctk.CTkButton(botones, text="Cancelar", width=100, command=self.destroy).pack(
            side="left", padx=8
        )
        ctk.CTkButton(botones, text="Asignar", width=100, command=self._on_guardar).pack(
            side="left", padx=8
        )
        self.after(10, self._configurar_modal)

    def _configurar_modal(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

    def _on_guardar(self) -> None:
        label = self._turno_var.get()
        turno_id = self._opciones_map.get(label)
        fecha = self._fecha_var.get().strip()
        if turno_id is None:
            self._error_label.configure(text="Seleccioná un turno válido.")
            return
        if not fecha:
            self._error_label.configure(text="La fecha no puede estar vacía.")
            return
        err = self._asignar(turno_id, fecha)
        if err is not None:
            self._error_label.configure(text=err)
            return
        self.destroy()
