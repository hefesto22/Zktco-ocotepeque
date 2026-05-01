"""Vista de Empleados (Sub-2.6).

Lista el catálogo de empleados con búsqueda por texto + filtro por
departamento + checkbox "Ver archivados". Cada fila expone los botones
Editar, Cambiar/Asignar turno y Dar de baja (o Reactivar si archivado).

El permiso ``MANAGE_EMPLOYEES`` vive en el controller (cada método
decorado con ``@require_permission``) — esta vista solo arma widgets
e invoca callables del controller.

Diseño visual:

    ┌───────────────────────────────────────────────────────────────────┐
    │  Empleados                                                         │
    ├───────────────────────────────────────────────────────────────────┤
    │  [Buscar...]    Depto: [combo ▾]   [🔲 Archivados]  [+ Nuevo]     │
    ├───────────────────────────────────────────────────────────────────┤
    │  Pérez, Juan · 0801-... · Administración · Cajero                 │
    │                                          [Editar] [Turno] [Baja]  │
    │  ...                                                               │
    └───────────────────────────────────────────────────────────────────┘

Pattern de resolución:

    La vista precarga (una vez por refresh) los catálogos completos —
    activos + archivados — y construye dicts ``id -> nombre`` para
    resolver el departamento y el cargo de cada empleado en O(1) sin
    N+1 queries a la BD.
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

from core.models.empleado import Empleado
from core.services.errors import (
    CatalogoNotFoundError,
    DuplicateDNIError,
    DuplicateZktecoIdError,
    EmpleadoAlreadyActiveError,
    EmpleadoAlreadyInactiveError,
    EmpleadoNotFoundError,
    InvalidDateError,
    InvalidDNIError,
    InvalidMotivoBajaError,
    MissingRequiredFieldError,
    SinTurnoVigenteError,
    TurnoInactiveError,
    TurnoNotFoundError,
    TurnoYaAsignadoError,
)
from ui.components.asignar_turno_dialog import (
    AsignarTurnoDialog,
    AsignarTurnoPayload,
)
from ui.components.dar_de_baja_dialog import (
    DarDeBajaDialog,
    DarDeBajaPayload,
)
from ui.components.empleado_form_dialog import (
    ComboOption,
    EmpleadoFormDialog,
    EmpleadoFormPayload,
)
from ui.controllers.empleados_controller import EmpleadosController

_FILTRO_DEPTO_TODOS = "Todos los departamentos"


class EmpleadosView(ctk.CTkFrame):
    """Vista raíz del módulo Empleados."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: EmpleadosController,
    ) -> None:
        """Construye la vista.

        Args:
            master: Contenedor Tk donde se monta.
            controller: Controller ya inyectado con session + servicios.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._log = logging.getLogger(self.__class__.__name__)

        self._ver_archivados_var = ctk.BooleanVar(value=False)
        self._filtro_depto_var = ctk.StringVar(value=_FILTRO_DEPTO_TODOS)
        self._query_var = ctk.StringVar(value="")

        # Mapas de resolución id -> nombre, se rellenan en cada refresh.
        self._deps_map: Dict[int, str] = {}
        self._cargos_map: Dict[int, str] = {}

        self._construir_ui()
        self._refrescar()

    # ── Construcción ──────────────────────────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma título, toolbar y lista scrollable."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Empleados",
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
        """Busqueda + filtro depto + checkbox archivados + nuevo."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(1, weight=1)

        # Entry de búsqueda.
        ctk.CTkLabel(bar, text="Buscar:").grid(row=0, column=0, padx=(0, 4), sticky="w")
        entry = ctk.CTkEntry(
            bar,
            textvariable=self._query_var,
            placeholder_text="DNI, nombre o apellido",
            width=260,
        )
        entry.grid(row=0, column=1, sticky="w", padx=(0, 12))
        entry.bind("<KeyRelease>", lambda _e: self._refrescar(recargar_catalogos=False))

        # Combo de filtro por departamento — lleno en _refrescar.
        ctk.CTkLabel(bar, text="Depto:").grid(row=0, column=2, padx=(0, 4), sticky="w")
        self._combo_filtro_depto = ctk.CTkOptionMenu(
            bar,
            values=[_FILTRO_DEPTO_TODOS],
            variable=self._filtro_depto_var,
            command=lambda _v: self._refrescar(recargar_catalogos=False),
            width=200,
        )
        self._combo_filtro_depto.grid(row=0, column=3, padx=(0, 12), sticky="w")

        # Checkbox ver archivados.
        chk = ctk.CTkCheckBox(
            bar,
            text="Ver archivados",
            variable=self._ver_archivados_var,
            command=self._refrescar,
        )
        chk.grid(row=0, column=4, sticky="w", padx=(0, 12))

        # Nuevo empleado.
        btn_nuevo = ctk.CTkButton(bar, text="+ Nuevo empleado", width=160, command=self._on_nuevo)
        btn_nuevo.grid(row=0, column=5, sticky="e")

        return bar

    # ── Carga de catálogos ────────────────────────────────────────────────

    def _recargar_catalogos(self) -> None:
        """Refresca los mapas id->nombre y el combo de filtro de depto.

        Se llama en refresh completo (no cuando el usuario solo tipea
        en el buscador o cambia el filtro de depto). Se traen TODOS los
        departamentos y cargos (activos + archivados) porque un empleado
        vigente puede pertenecer a un catálogo que fue archivado después.
        """
        deps = self._controller.list_departamentos(solo_activos=False)
        cargos = self._controller.list_cargos(solo_activos=False)
        self._deps_map = {d.id: d.nombre for d in deps if d.id is not None}
        self._cargos_map = {c.id: c.nombre for c in cargos if c.id is not None}

        # Rellenamos el combo de filtro con los ACTIVOS — no tiene
        # sentido filtrar por un departamento que ya no existe, salvo
        # que el usuario marque "Ver archivados", pero ese caso es
        # tangencial. Mantenerlo simple.
        deps_activos = [d for d in deps if d.is_active]
        opciones = [_FILTRO_DEPTO_TODOS] + [d.nombre for d in deps_activos]
        self._combo_filtro_depto.configure(values=opciones)
        if self._filtro_depto_var.get() not in opciones:
            self._filtro_depto_var.set(_FILTRO_DEPTO_TODOS)

    # ── Render de la lista ────────────────────────────────────────────────

    def _refrescar(self, recargar_catalogos: bool = True) -> None:
        """Lee del controller y re-pinta toda la lista.

        Args:
            recargar_catalogos: Si ``False`` reutiliza los mapas ya
                cargados (útil para filtros del lado del cliente que
                no requieren re-consultar la BD).
        """
        for hijo in self._lista.winfo_children():
            hijo.destroy()

        if recargar_catalogos or not self._deps_map:
            try:
                self._recargar_catalogos()
            except Exception:  # noqa: BLE001
                self._log.exception("Error cargando catálogos")
                messagebox.showerror(
                    "Error", "No se pudieron cargar los catálogos de departamento/cargo."
                )
                return

        solo_activos = not self._ver_archivados_var.get()
        try:
            empleados = self._controller.list_empleados(solo_activos=solo_activos)
        except Exception:  # noqa: BLE001
            self._log.exception("Error listando empleados")
            messagebox.showerror("Error", "No se pudo cargar la lista de empleados.")
            return

        empleados = self._filtrar_client_side(empleados)

        if not empleados:
            self._pintar_vacio()
            return

        for idx, emp in enumerate(empleados):
            self._pintar_fila(idx, emp)

    def _filtrar_client_side(self, empleados: List[Empleado]) -> List[Empleado]:
        """Aplica los filtros de texto y departamento sobre la lista."""
        # Los ``StringVar.get()`` devuelven ``Any`` en los stubs de tkinter —
        # las anotaciones explícitas colapsan ese ``Any`` a ``str`` para que
        # mypy --strict acepte los retornos de los predicados.
        query: str = self._query_var.get().strip().lower()
        depto_filtro: str = self._filtro_depto_var.get()

        def coincide_texto(emp: Empleado) -> bool:
            if not query:
                return True
            hay = " ".join(
                [
                    emp.dni.lower(),
                    emp.nombres.lower(),
                    emp.apellidos.lower(),
                ]
            )
            return query in hay

        def coincide_depto(emp: Empleado) -> bool:
            if depto_filtro == _FILTRO_DEPTO_TODOS:
                return True
            return self._deps_map.get(emp.departamento_id) == depto_filtro

        return [e for e in empleados if coincide_texto(e) and coincide_depto(e)]

    def _pintar_vacio(self) -> None:
        """Muestra un placeholder cuando la lista está vacía."""
        msg = ctk.CTkLabel(
            self._lista,
            text="No hay empleados que coincidan con el filtro.",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
        )
        msg.grid(row=0, column=0, sticky="w", padx=12, pady=12)

    def _pintar_fila(self, idx: int, emp: Empleado) -> None:
        """Dibuja una fila para un empleado."""
        fila = ctk.CTkFrame(self._lista, fg_color=("gray90", "gray20"))
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)

        texto = self._formato_resumen(emp)
        if not emp.is_active:
            texto = f"{texto}   (archivado)"
        color = ("gray10", "gray90") if emp.is_active else ("gray45", "gray55")

        label = ctk.CTkLabel(fila, text=texto, text_color=color, anchor="w")
        label.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 2))

        # Botones en la siguiente fila para no comprimir la info.
        botones = ctk.CTkFrame(fila, fg_color="transparent")
        botones.grid(row=1, column=0, sticky="e", padx=8, pady=(0, 6))

        if emp.id is None:
            return

        if emp.is_active:
            ctk.CTkButton(
                botones,
                text="Editar",
                width=90,
                command=lambda e=emp: self._on_editar(e),
            ).grid(row=0, column=0, padx=4)

            ctk.CTkButton(
                botones,
                text="Turno",
                width=90,
                command=lambda e=emp: self._on_asignar_o_cambiar_turno(e),
            ).grid(row=0, column=1, padx=4)

            ctk.CTkButton(
                botones,
                text="Dar de baja",
                width=110,
                fg_color=("#b3261e", "#cf6679"),
                hover_color=("#8c1d18", "#a24a5a"),
                command=lambda e=emp: self._on_dar_de_baja(e),
            ).grid(row=0, column=2, padx=4)
        else:
            ctk.CTkButton(
                botones,
                text="Reactivar",
                width=100,
                command=lambda e=emp: self._on_reactivar(e),
            ).grid(row=0, column=0, padx=4)

    # ── Handlers: acciones sobre un empleado ──────────────────────────────

    def _cargos_por_depto_provider(self, departamento_id: int) -> List[Tuple[int, str]]:
        """Sub-3.2.A: provider que el diálogo usa para refrescar cargos.

        Devuelve la lista de cargos elegibles para el departamento dado
        (globales + específicos), en formato ``(id, nombre)`` que el
        dropdown del formulario espera.
        """
        try:
            cargos = self._controller.list_cargos_para_departamento(departamento_id)
        except Exception:  # noqa: BLE001
            self._log.exception("No se pudieron cargar cargos para depto=%s", departamento_id)
            return []
        return [(c.id, c.nombre) for c in cargos if c.id is not None]

    def _on_nuevo(self) -> None:
        """Abre el diálogo en modo alta y delega la creación al controller."""
        try:
            deps = [
                (d.id, d.nombre)
                for d in self._controller.list_departamentos(solo_activos=True)
                if d.id is not None
            ]
            cargos = [
                (c.id, c.nombre)
                for c in self._controller.list_cargos(solo_activos=True)
                if c.id is not None
            ]
            turnos = [
                (t.id, t.nombre)
                for t in self._controller.list_turnos(solo_activos=True)
                if t.id is not None
            ]
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando catálogos para Nuevo empleado")
            messagebox.showerror("Error", "No se pudieron cargar los catálogos.")
            return

        if not deps or not cargos:
            messagebox.showwarning(
                "Catálogos incompletos",
                "Debe existir al menos un departamento y un cargo activos antes "
                "de crear empleados. Configúrelos en el módulo de Configuración.",
            )
            return
        if not turnos:
            messagebox.showwarning(
                "Sin turnos activos",
                "Debe existir al menos un turno activo antes de crear empleados. "
                "Configúrelo en el módulo de Turnos.",
            )
            return

        dialog = EmpleadoFormDialog(
            self,
            title="Nuevo empleado",
            on_submit=self._handle_create_con_turno,
            departamentos=deps,
            cargos=cargos,
            turnos=turnos,
            modo_alta=True,
            cargos_por_depto_provider=self._cargos_por_depto_provider,
        )
        self.wait_window(dialog)
        self._refrescar()

    def _on_editar(self, emp: Empleado) -> None:
        """Abre el diálogo en modo edición precargado con el empleado."""
        assert emp.id is not None
        emp_id = emp.id
        try:
            deps = [
                (d.id, d.nombre)
                for d in self._controller.list_departamentos(solo_activos=True)
                if d.id is not None
            ]
            cargos = [
                (c.id, c.nombre)
                for c in self._controller.list_cargos(solo_activos=True)
                if c.id is not None
            ]
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando catálogos para Editar empleado")
            messagebox.showerror("Error", "No se pudieron cargar los catálogos.")
            return

        def submit(payload: EmpleadoFormPayload) -> Optional[str]:
            return self._handle_update(emp_id, payload)

        dialog = EmpleadoFormDialog(
            self,
            title=f"Editar empleado — {emp.nombres} {emp.apellidos}",
            on_submit=submit,
            departamentos=deps,
            cargos=cargos,
            turnos=None,
            modo_alta=False,
            initial_dni=emp.dni,
            initial_nombres=emp.nombres,
            initial_apellidos=emp.apellidos,
            initial_departamento_id=emp.departamento_id,
            initial_cargo_id=emp.cargo_id,
            initial_fecha_ingreso=emp.fecha_ingreso,
            initial_telefono=emp.telefono or "",
            initial_email=emp.email or "",
            initial_zkteco_id=emp.zkteco_id,
            cargos_por_depto_provider=self._cargos_por_depto_provider,
        )
        self.wait_window(dialog)
        self._refrescar()

    def _on_asignar_o_cambiar_turno(self, emp: Empleado) -> None:
        """Decide entre ``asignar_turno`` y ``cambiar_turno`` según el estado."""
        assert emp.id is not None
        emp_id = emp.id
        try:
            vigente = self._controller.get_turno_vigente(emp_id)
            turnos_options: List[ComboOption] = [
                (t.id, t.nombre)
                for t in self._controller.list_turnos(solo_activos=True)
                if t.id is not None
            ]
        except Exception:  # noqa: BLE001
            self._log.exception("Error cargando datos para asignar turno (emp=%s)", emp_id)
            messagebox.showerror("Error", "No se pudo cargar la información de turnos.")
            return

        if not turnos_options:
            messagebox.showwarning(
                "Sin turnos activos",
                "No hay turnos activos disponibles. Configure al menos uno antes " "de asignar.",
            )
            return

        modo_cambiar = vigente is not None
        descripcion_actual: Optional[str] = None
        if vigente is not None:
            nombre_turno_actual = self._resolver_nombre_turno(vigente.turno_id, turnos_options)
            descripcion_actual = f"{nombre_turno_actual} (desde {vigente.fecha_inicio})"

        def submit(payload: AsignarTurnoPayload) -> Optional[str]:
            if modo_cambiar:
                return self._handle_cambiar_turno(emp_id, payload)
            return self._handle_asignar_turno(emp_id, payload)

        dialog = AsignarTurnoDialog(
            self,
            on_submit=submit,
            empleado_descripcion=self._empleado_titulo(emp),
            turnos=turnos_options,
            modo_cambiar=modo_cambiar,
            turno_actual_descripcion=descripcion_actual,
        )
        self.wait_window(dialog)
        self._refrescar()

    def _on_dar_de_baja(self, emp: Empleado) -> None:
        """Abre el diálogo de baja con confirmación explícita."""
        assert emp.id is not None
        emp_id = emp.id

        def submit(payload: DarDeBajaPayload) -> Optional[str]:
            return self._handle_dar_de_baja(emp_id, payload)

        dialog = DarDeBajaDialog(
            self,
            on_submit=submit,
            empleado_descripcion=self._empleado_titulo(emp),
        )
        self.wait_window(dialog)
        self._refrescar()

    def _on_reactivar(self, emp: Empleado) -> None:
        """Reactiva un empleado archivado (sin restaurar historial de turno)."""
        assert emp.id is not None
        confirmado = messagebox.askyesno(
            "Reactivar empleado",
            (
                f"¿Reactivar a {emp.nombres} {emp.apellidos}?\n\n"
                "El empleado vuelve a aparecer en las listas activas, pero "
                "no se restaura su asignación de turno automáticamente — "
                "tendrá que asignarle un turno nuevo."
            ),
        )
        if not confirmado:
            return
        try:
            self._controller.reactivate_empleado(emp.id)
        except EmpleadoNotFoundError as exc:
            messagebox.showerror("Empleado no encontrado", str(exc))
            return
        except EmpleadoAlreadyActiveError as exc:
            messagebox.showwarning("Ya está activo", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando empleado id=%s", emp.id)
            messagebox.showerror("Error", "No se pudo reactivar el empleado.")
            return
        self._refrescar()

    # ── Callbacks de los diálogos (delegan al controller) ─────────────────

    def _handle_create_con_turno(self, payload: EmpleadoFormPayload) -> Optional[str]:
        """Invoca ``create_empleado_con_turno``. None=OK, str=error."""
        # En modo alta, turno_id y fecha_inicio_turno están garantizados.
        turno_id = payload.get("turno_id")
        fecha_inicio_turno = payload.get("fecha_inicio_turno")
        if turno_id is None or fecha_inicio_turno is None:
            return "Falta seleccionar el turno inicial."

        try:
            resultado = self._controller.create_empleado_con_turno(
                dni=payload["dni"],
                nombres=payload["nombres"],
                apellidos=payload["apellidos"],
                departamento_id=payload["departamento_id"],
                cargo_id=payload["cargo_id"],
                fecha_ingreso=payload["fecha_ingreso"],
                turno_id=turno_id,
                fecha_inicio_turno=fecha_inicio_turno,
                telefono=payload["telefono"],
                email=payload["email"],
                zkteco_id=payload["zkteco_id"],
            )
        except (
            MissingRequiredFieldError,
            InvalidDNIError,
            InvalidDateError,
            DuplicateDNIError,
            DuplicateZktecoIdError,
            CatalogoNotFoundError,
            TurnoNotFoundError,
            TurnoInactiveError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error creando empleado")
            return "Ocurrió un error inesperado al crear el empleado."

        if resultado.warning:
            # El empleado quedó creado pero sin turno vigente — se lo
            # avisamos al usuario y seguimos. Cerramos el diálogo.
            messagebox.showwarning("Empleado creado con aviso", resultado.warning)
        return None

    def _handle_update(self, emp_id: int, payload: EmpleadoFormPayload) -> Optional[str]:
        """Invoca ``update_empleado``. None=OK, str=error."""
        try:
            self._controller.update_empleado(
                empleado_id=emp_id,
                dni=payload["dni"],
                nombres=payload["nombres"],
                apellidos=payload["apellidos"],
                departamento_id=payload["departamento_id"],
                cargo_id=payload["cargo_id"],
                fecha_ingreso=payload["fecha_ingreso"],
                telefono=payload["telefono"],
                email=payload["email"],
                zkteco_id=payload["zkteco_id"],
            )
        except (
            MissingRequiredFieldError,
            InvalidDNIError,
            InvalidDateError,
            DuplicateDNIError,
            DuplicateZktecoIdError,
            CatalogoNotFoundError,
            EmpleadoNotFoundError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error actualizando empleado id=%s", emp_id)
            return "Ocurrió un error inesperado al guardar el empleado."
        return None

    def _handle_asignar_turno(self, emp_id: int, payload: AsignarTurnoPayload) -> Optional[str]:
        """Invoca ``asignar_turno``. None=OK, str=error."""
        try:
            self._controller.asignar_turno(
                empleado_id=emp_id,
                turno_id=payload["turno_id"],
                fecha_inicio=payload["fecha_inicio"],
            )
        except (
            EmpleadoNotFoundError,
            TurnoNotFoundError,
            TurnoInactiveError,
            TurnoYaAsignadoError,
            InvalidDateError,
            MissingRequiredFieldError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error asignando turno a empleado id=%s", emp_id)
            return "Ocurrió un error inesperado al asignar el turno."
        return None

    def _handle_cambiar_turno(self, emp_id: int, payload: AsignarTurnoPayload) -> Optional[str]:
        """Invoca ``cambiar_turno``. None=OK, str=error."""
        try:
            self._controller.cambiar_turno(
                empleado_id=emp_id,
                turno_nuevo_id=payload["turno_id"],
                fecha_inicio_nueva=payload["fecha_inicio"],
            )
        except (
            EmpleadoNotFoundError,
            TurnoNotFoundError,
            TurnoInactiveError,
            SinTurnoVigenteError,
            InvalidDateError,
            MissingRequiredFieldError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error cambiando turno a empleado id=%s", emp_id)
            return "Ocurrió un error inesperado al cambiar el turno."
        return None

    def _handle_dar_de_baja(self, emp_id: int, payload: DarDeBajaPayload) -> Optional[str]:
        """Invoca ``deactivate_empleado``. None=OK, str=error."""
        try:
            self._controller.deactivate_empleado(
                empleado_id=emp_id,
                fecha_baja=payload["fecha_baja"],
                motivo_baja=payload["motivo_baja"],
                nota_baja=payload["nota_baja"],
            )
        except (
            EmpleadoNotFoundError,
            EmpleadoAlreadyInactiveError,
            InvalidMotivoBajaError,
            InvalidDateError,
            MissingRequiredFieldError,
        ) as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Error dando de baja empleado id=%s", emp_id)
            return "Ocurrió un error inesperado al dar de baja al empleado."
        return None

    # ── Helpers de formato ────────────────────────────────────────────────

    def _formato_resumen(self, emp: Empleado) -> str:
        """Resume un empleado en una línea legible para la lista."""
        dep = self._deps_map.get(emp.departamento_id, "(depto desconocido)")
        cargo = self._cargos_map.get(emp.cargo_id, "(cargo desconocido)")
        return f"{emp.apellidos}, {emp.nombres}   ·   DNI {emp.dni}   ·   " f"{dep}   ·   {cargo}"

    @staticmethod
    def _empleado_titulo(emp: Empleado) -> str:
        """Texto corto identificando al empleado para cabeceras de diálogo."""
        return f"{emp.nombres} {emp.apellidos} (DNI {emp.dni})"

    @staticmethod
    def _resolver_nombre_turno(turno_id: int, opciones: List[ComboOption]) -> str:
        """Busca el nombre de un turno dentro de una lista de opciones."""
        for id_, nombre in opciones:
            if id_ == turno_id:
                return nombre
        return f"(turno #{turno_id})"
