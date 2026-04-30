"""Vista de Asistencia (Sub-3.4b + Sub-3.4c).

Permite al usuario (OPERADOR o rol superior con ``VIEW_ATTENDANCE``)
consultar las asistencias consolidadas en un rango de fechas, filtrar
por empleado y editar la observación manual de cada fila. Los roles
con ``RUN_ZKTECO_SYNC`` (OPERADOR, ADMIN, SUPERADMIN) ven además el
botón "Re-consolidar" para regenerar manualmente las asistencias del
rango tras editar turnos o feriados — respeta el filtro de empleado
activo y pide confirmación explícita antes de ejecutar.

Diseño visual:

    ┌────────────────────────────────────────────────────────────┐
    │  Asistencia                                                 │
    ├────────────────────────────────────────────────────────────┤
    │  Empleado: [CTkComboBox ▼]   Desde: [📅]   Hasta: [📅]      │
    │                                           [   Cargar   ]    │
    ├────────────────────────────────────────────────────────────┤
    │  Estado: [spinner + label]                                  │
    │  [banner truncamiento, si aplica]                           │
    ├────────────────────────────────────────────────────────────┤
    │  Fecha    │ Empleado  │ Turno │ Entr. │ Salida │ Estado ●  │
    │  2026-... │ Pérez J.  │ Adm.  │ 08:00 │ 17:05  │ ● Presente│
    │  ...                                                        │
    └────────────────────────────────────────────────────────────┘

Cada fila tiene un botón "Editar obs" que abre ``ObservacionDialog``.
El estado se muestra con un "dot" de color + texto en español, para
mantener accesibilidad (no confiamos solo en color).

Threading:
    La carga de asistencias es una query local pero puede ser hasta 500
    filas + N lookups de catálogo. Se envuelve en ``run_async_ui`` para
    que la UI no se bloquee.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from tkinter import messagebox
from typing import Callable, List, Optional, Tuple

import customtkinter as ctk
from tkcalendar import DateEntry

from core.models.asistencia import EstadoAsistencia
from core.services.asistencia_service import AsistenciaVista, ResultadoBusquedaAsistencia
from core.services.errors import (
    AsistenciaNotFoundError,
    EmpleadoNotFoundError,
    InvalidDateError,
    InvalidRangoError,
)
from core.services.sincronizacion_result import ResultadoConsolidacion
from ui.async_util import run_async_ui
from ui.components.observacion_dialog import ObservacionDialog
from ui.controllers.asistencia_controller import AsistenciaController


# Sentinel para "todos los empleados" en el combo de filtro.
_LABEL_TODOS: str = "(Todos los empleados)"

# Rango default al montar la vista: últimos 7 días (hoy-6 → hoy).
# Decisión aprobada en Sub-3.4b.
_RANGO_DEFAULT_DIAS: int = 6


# Mapa EstadoAsistencia.value → (label en español, color del dot).
# El color es una tupla (light_mode, dark_mode) para respetar el tema
# del sistema. Usamos paleta Material 500/300 para buen contraste en
# ambos modos. El texto del estado queda en el foreground default del
# tema — nunca texto coloreado sobre fondo coloreado (accesibilidad).
_ESTADO_STYLE: dict[str, Tuple[str, Tuple[str, str]]] = {
    EstadoAsistencia.PRESENTE.value: ("Presente", ("#2e7d32", "#66bb6a")),
    EstadoAsistencia.TARDE.value: ("Tarde", ("#f57f17", "#ffca28")),
    EstadoAsistencia.SALIDA_TEMPRANA.value: ("Salida temprana", ("#f57f17", "#ffca28")),
    EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value: (
        "Tarde + salida temprana",
        ("#e65100", "#ff8a65"),
    ),
    EstadoAsistencia.AUSENTE.value: ("Ausente", ("#b71c1c", "#ef5350")),
    EstadoAsistencia.SIN_TURNO.value: ("Sin turno", ("#616161", "#9e9e9e")),
    EstadoAsistencia.FERIADO.value: ("Feriado", ("#1565c0", "#64b5f6")),
    EstadoAsistencia.INCOMPLETO.value: ("Incompleto", ("#ef6c00", "#ffb74d")),
}

# Fallback para estados no mapeados (defensivo — si el enum crece y
# olvidamos actualizar el mapa). Nunca debería dispararse con el código
# actual, pero evita un KeyError en caliente.
_ESTADO_STYLE_FALLBACK: Tuple[str, Tuple[str, str]] = ("Desconocido", ("gray50", "gray60"))

# Anchos de columna (caracteres) para las celdas de la tabla. Se usan
# para que las filas de datos se alineen con la cabecera — grid con
# weight no basta porque las labels varían de largo.
_COL_FECHA_W: int = 12
_COL_EMPLEADO_W: int = 24
_COL_TURNO_W: int = 14
_COL_HORA_W: int = 8
_COL_ESTADO_W: int = 24
_COL_OBS_W: int = 28


class AsistenciaView(ctk.CTkFrame):
    """Vista raíz del módulo Asistencia."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: AsistenciaController,
    ) -> None:
        """Construye la vista.

        Args:
            master: Contenedor Tk donde se monta.
            controller: Controller ya inyectado con session + servicio.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._log = logging.getLogger(self.__class__.__name__)

        # Cache del combo de empleados: label → id (None = "(Todos)").
        self._empleado_id_por_label: dict[str, Optional[int]] = {}
        # Último rango consultado — se usa para recargar tras editar
        # una observación sin que el usuario tenga que re-pulsar "Cargar".
        self._ultimo_desde: Optional[str] = None
        self._ultimo_hasta: Optional[str] = None
        self._ultimo_empleado_id: Optional[int] = None

        self._construir_ui()
        self._cargar_empleados_combo()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma título, form, status bar, banner y área de resultados."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Asistencia",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))

        self._form = self._construir_form()
        self._form.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        self._status = self._construir_status()
        self._status.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))

        # Banner de truncamiento (oculto al inicio, se muestra si aplica).
        self._banner = ctk.CTkFrame(self, fg_color=("#fff3cd", "#4a3d10"))
        self._banner_label = ctk.CTkLabel(
            self._banner,
            text="",
            text_color=("#664d03", "#ffe69c"),
            anchor="w",
            font=ctk.CTkFont(size=12),
            wraplength=900,
            justify="left",
        )
        self._banner_label.pack(padx=12, pady=8, fill="x")

        self._resultados = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._resultados.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._resultados.grid_columnconfigure(0, weight=1)
        self._pintar_estado_inicial()

    def _construir_form(self) -> ctk.CTkFrame:
        """Arma el formulario de filtros (combo empleado, dates, botón)."""
        form = ctk.CTkFrame(self, fg_color=("gray92", "gray18"))
        form.grid_columnconfigure(5, weight=1)

        pad_x = 8
        pad_y = 10

        ctk.CTkLabel(form, text="Empleado:", anchor="w").grid(
            row=0, column=0, sticky="w", padx=(12, pad_x), pady=pad_y
        )
        self._combo_empleado = ctk.CTkComboBox(
            form,
            values=[_LABEL_TODOS],
            width=240,
            state="readonly",
        )
        self._combo_empleado.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=pad_y)
        self._combo_empleado.set(_LABEL_TODOS)

        ctk.CTkLabel(form, text="Desde:", anchor="w").grid(
            row=0, column=2, sticky="w", padx=(0, pad_x), pady=pad_y
        )
        self._date_desde = self._construir_date(form, column=3)

        ctk.CTkLabel(form, text="Hasta:", anchor="w").grid(
            row=0, column=4, sticky="w", padx=(16, pad_x), pady=pad_y
        )
        self._date_hasta = self._construir_date(form, column=5)

        self._btn_cargar = ctk.CTkButton(
            form,
            text="Cargar",
            command=self._on_cargar,
            height=32,
            width=120,
        )
        self._btn_cargar.grid(row=0, column=6, sticky="e", padx=(12, 6), pady=pad_y)

        # Botón de re-consolidación manual: solo visible si el rol
        # tiene ``RUN_ZKTECO_SYNC`` (OPERADOR, ADMIN, SUPERADMIN).
        # Visualmente secundario al Cargar — estilo outline gris para
        # dejar claro que es una acción "distinta" (regenera datos).
        self._btn_re_consolidar: Optional[ctk.CTkButton] = None
        if self._controller.puede_re_consolidar():
            self._btn_re_consolidar = ctk.CTkButton(
                form,
                text="Re-consolidar",
                command=self._on_re_consolidar,
                height=32,
                width=130,
                fg_color=("gray75", "gray30"),
                hover_color=("gray65", "gray40"),
                text_color=("gray10", "gray90"),
            )
            self._btn_re_consolidar.grid(row=0, column=7, sticky="e", padx=(0, 12), pady=pad_y)

        self._inicializar_rango_default()
        return form

    def _construir_date(self, parent: ctk.CTkFrame, column: int) -> DateEntry:
        """Arma un DateEntry para el grid del formulario."""
        picker = DateEntry(
            parent,
            date_pattern="yyyy-mm-dd",
            width=12,
            background="darkblue",
            foreground="white",
            borderwidth=2,
        )
        picker.grid(row=0, column=column, sticky="w", padx=(0, 0), pady=10)
        return picker

    def _inicializar_rango_default(self) -> None:
        """Setea los DateEntry a los últimos 7 días (hoy-6 → hoy)."""
        hoy = date.today()
        desde = hoy - timedelta(days=_RANGO_DEFAULT_DIAS)
        self._date_desde.set_date(desde)
        self._date_hasta.set_date(hoy)

    def _construir_status(self) -> ctk.CTkFrame:
        """Arma la barra de estado con label y spinner."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(1, weight=1)
        self._status_label = ctk.CTkLabel(
            bar,
            text="Seleccione un rango y presione Cargar.",
            anchor="w",
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=12),
        )
        self._status_label.grid(row=0, column=0, sticky="w", padx=12)
        self._spinner = ctk.CTkProgressBar(bar, mode="indeterminate", width=160)
        # El spinner se hace ``grid`` solo cuando está activo.
        return bar

    # ── Carga inicial del combo de empleados ──────────────────────────────

    def _cargar_empleados_combo(self) -> None:
        """Llama al controller y pobla el combo con los empleados activos."""
        try:
            tuplas = self._controller.list_empleados_para_filtro()
        except Exception:  # noqa: BLE001 — último recurso
            self._log.exception("Error listando empleados para filtro")
            messagebox.showerror(
                "Error",
                "No se pudo cargar la lista de empleados. Intente de "
                "nuevo o consulte con el administrador.",
            )
            return
        self._poblar_combo_empleados(tuplas)

    def _poblar_combo_empleados(self, tuplas: List[Tuple[int, str]]) -> None:
        """Configura el combo. ``_LABEL_TODOS`` siempre va primero."""
        # Construimos el dict label→id. "(Todos)" mapea a None.
        self._empleado_id_por_label = {_LABEL_TODOS: None}
        for emp_id, nombre in tuplas:
            # Si dos empleados tienen el mismo nombre formateado,
            # desambiguamos con el id. Poco probable pero defensivo.
            label = nombre if nombre not in self._empleado_id_por_label else f"{nombre} (#{emp_id})"
            self._empleado_id_por_label[label] = emp_id
        self._combo_empleado.configure(values=list(self._empleado_id_por_label.keys()))
        self._combo_empleado.set(_LABEL_TODOS)

    # ── Handler del botón Cargar ──────────────────────────────────────────

    def _on_cargar(self) -> None:
        """Dispara la query en un thread, no bloquea la UI."""
        desde = self._date_desde.get_date().isoformat()
        hasta = self._date_hasta.get_date().isoformat()
        empleado_id = self._empleado_id_por_label.get(self._combo_empleado.get())

        if desde > hasta:
            messagebox.showwarning(
                "Rango inválido",
                "La fecha 'Desde' no puede ser posterior a 'Hasta'.",
            )
            return

        self._bloquear_controles()
        self._ultimo_desde = desde
        self._ultimo_hasta = hasta
        self._ultimo_empleado_id = empleado_id

        run_async_ui(
            self,
            work=lambda: self._controller.list_asistencias(
                desde=desde,
                hasta=hasta,
                empleado_id=empleado_id,
            ),
            on_success=self._on_cargar_ok,
            on_error=self._on_cargar_err,
        )

    def _bloquear_controles(self) -> None:
        """Deshabilita el botón y activa el spinner mientras corre la query."""
        self._btn_cargar.configure(state="disabled", text="Cargando...")
        self._spinner.grid(row=0, column=1, sticky="e", padx=12)
        self._spinner.start()
        self._status_label.configure(
            text="Consultando asistencias...",
            text_color=("gray20", "gray80"),
        )

    def _desbloquear_controles(self) -> None:
        """Revierte el estado del form tras terminar la query."""
        self._spinner.stop()
        self._spinner.grid_forget()
        self._btn_cargar.configure(state="normal", text="Cargar")

    def _on_cargar_ok(self, resultado: ResultadoBusquedaAsistencia) -> None:
        """Callback en UI thread tras una query exitosa."""
        self._desbloquear_controles()
        cantidad = len(resultado.items)
        self._status_label.configure(
            text=f"{cantidad} fila(s) cargada(s).",
            text_color=("#1b5e20", "#a5d6a7") if cantidad else ("gray30", "gray70"),
        )
        self._pintar_banner_truncamiento(resultado.truncado, cantidad)
        self._pintar_resultados(resultado.items)

    def _on_cargar_err(self, exc: BaseException) -> None:
        """Callback en UI thread tras un fallo de la query."""
        self._desbloquear_controles()
        self._log.exception("Error cargando asistencias", exc_info=exc)
        self._status_label.configure(
            text="Error: no se pudo cargar la lista.",
            text_color=("#b71c1c", "#ef9a9a"),
        )
        messagebox.showerror(
            "Error al cargar",
            "Ocurrió un error inesperado al consultar las asistencias. "
            "Revise el log para detalles.",
        )

    # ── Render del banner de truncamiento ─────────────────────────────────

    def _pintar_banner_truncamiento(self, truncado: bool, cantidad: int) -> None:
        """Muestra/oculta el banner si el resultado excedió el límite."""
        if truncado:
            texto = (
                f"Se muestran las primeras {cantidad} filas. El rango "
                f"tiene más resultados — acote el rango o filtre por un "
                f"empleado para ver todos."
            )
            self._banner_label.configure(text=texto)
            self._banner.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        else:
            self._banner.grid_forget()

    # ── Render del área de resultados ─────────────────────────────────────

    def _pintar_estado_inicial(self) -> None:
        """Placeholder mostrado al montar la vista antes de la primera query."""
        for hijo in self._resultados.winfo_children():
            hijo.destroy()
        msg = ctk.CTkLabel(
            self._resultados,
            text=(
                "Seleccione un rango de fechas (por defecto, últimos 7 "
                "días), opcionalmente un empleado, y presione Cargar."
            ),
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=800,
            justify="left",
            anchor="w",
        )
        msg.grid(row=0, column=0, sticky="w", padx=16, pady=16)

    def _pintar_resultados(self, items: List[AsistenciaVista]) -> None:
        """Re-pinta el área de resultados con la lista cargada."""
        for hijo in self._resultados.winfo_children():
            hijo.destroy()

        if not items:
            self._pintar_vacio()
            return

        self._pintar_cabecera()
        for idx, vista in enumerate(items, start=1):
            self._pintar_fila(idx, vista)

    def _pintar_vacio(self) -> None:
        """Mensaje cuando la query fue exitosa pero sin resultados."""
        msg = ctk.CTkLabel(
            self._resultados,
            text=(
                "No hay asistencias en el rango seleccionado. Verifique "
                "que la sincronización ZKTeco haya corrido o amplíe el "
                "rango."
            ),
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=800,
            justify="left",
            anchor="w",
        )
        msg.grid(row=0, column=0, sticky="w", padx=16, pady=16)

    def _pintar_cabecera(self) -> None:
        """Fila de encabezado con los nombres de las columnas."""
        cabecera = ctk.CTkFrame(self._resultados, fg_color=("gray85", "gray25"))
        cabecera.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        self._pintar_columnas_en_fila(
            cabecera,
            fecha="Fecha",
            empleado="Empleado",
            turno="Turno",
            entrada="Entrada",
            salida="Salida",
            estado_texto="Estado",
            observaciones="Observación",
            dot_color=None,
            bold=True,
            boton_command=None,
        )

    def _pintar_fila(self, idx: int, vista: AsistenciaVista) -> None:
        """Arma una fila de datos con las celdas + el botón Editar."""
        fila = ctk.CTkFrame(self._resultados, fg_color="transparent")
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=1)

        estado_label, dot_color = _ESTADO_STYLE.get(vista.asistencia.estado, _ESTADO_STYLE_FALLBACK)
        estado_texto = self._formatear_estado(vista, estado_label)

        self._pintar_columnas_en_fila(
            fila,
            fecha=vista.asistencia.fecha,
            empleado=vista.empleado_nombre_completo,
            turno=vista.turno_nombre or "—",
            entrada=self._formatear_hora(vista.asistencia.hora_entrada_real),
            salida=self._formatear_hora(vista.asistencia.hora_salida_real),
            estado_texto=estado_texto,
            observaciones=self._formatear_observaciones(vista.asistencia.observaciones),
            dot_color=dot_color,
            bold=False,
            boton_command=self._make_edit_handler(vista),
        )

    def _make_edit_handler(self, vista: AsistenciaVista) -> Callable[[], None]:
        """Crea un callback sin args que abre el diálogo para ``vista``.

        Se extrae en helper en vez de un lambda porque mypy --strict no
        puede inferir el tipo de un lambda con default-arg — la firma
        explícita aquí deja los tipos redondos.
        """

        def handler() -> None:
            self._abrir_dialogo_observacion(vista)

        return handler

    def _pintar_columnas_en_fila(  # noqa: PLR0913 — celdas de una fila tabular
        self,
        parent: ctk.CTkFrame,
        fecha: str,
        empleado: str,
        turno: str,
        entrada: str,
        salida: str,
        estado_texto: str,
        observaciones: str,
        dot_color: Optional[Tuple[str, str]],
        bold: bool,
        boton_command: Optional[Callable[[], None]],
    ) -> None:
        """Pinta las 8 columnas de una fila (datos o cabecera).

        Si ``dot_color`` es ``None`` y ``boton_command`` es ``None``,
        renderiza como cabecera — sin dot y sin botón.
        """
        parent.grid_columnconfigure(6, weight=1)  # la columna obs se estira
        font = ctk.CTkFont(size=12, weight="bold") if bold else ctk.CTkFont(size=12)

        self._celda(parent, fecha, column=0, width=_COL_FECHA_W, font=font)
        self._celda(parent, empleado, column=1, width=_COL_EMPLEADO_W, font=font)
        self._celda(parent, turno, column=2, width=_COL_TURNO_W, font=font)
        self._celda(parent, entrada, column=3, width=_COL_HORA_W, font=font)
        self._celda(parent, salida, column=4, width=_COL_HORA_W, font=font)
        self._celda_estado(parent, estado_texto, dot_color, column=5, font=font)
        self._celda(parent, observaciones, column=6, width=_COL_OBS_W, font=font)
        self._celda_boton(parent, boton_command, column=7)

    def _celda(
        self,
        parent: ctk.CTkFrame,
        texto: str,
        column: int,
        width: int,
        font: ctk.CTkFont,
    ) -> None:
        """Pinta una celda de texto con ancho fijo."""
        lbl = ctk.CTkLabel(parent, text=texto, anchor="w", width=width * 7, font=font)
        lbl.grid(row=0, column=column, sticky="w", padx=(6, 4), pady=3)

    def _celda_estado(
        self,
        parent: ctk.CTkFrame,
        texto: str,
        dot_color: Optional[Tuple[str, str]],
        column: int,
        font: ctk.CTkFont,
    ) -> None:
        """Pinta la celda de estado: dot de color + texto.

        Si ``dot_color`` es ``None`` (caso cabecera), solo texto.
        """
        contenedor = ctk.CTkFrame(parent, fg_color="transparent")
        contenedor.grid(row=0, column=column, sticky="w", padx=(6, 4), pady=3)
        if dot_color is not None:
            dot = ctk.CTkLabel(
                contenedor,
                text="",
                width=10,
                height=10,
                corner_radius=5,
                fg_color=dot_color,
            )
            dot.grid(row=0, column=0, padx=(0, 6))
        lbl = ctk.CTkLabel(
            contenedor,
            text=texto,
            anchor="w",
            width=_COL_ESTADO_W * 7,
            font=font,
        )
        lbl.grid(row=0, column=1, sticky="w")

    def _celda_boton(
        self,
        parent: ctk.CTkFrame,
        command: Optional[Callable[[], None]],
        column: int,
    ) -> None:
        """Pinta la última celda: botón "Editar" o vacío en la cabecera."""
        if command is None:
            # Cabecera: placeholder para que el ancho alinee con las filas.
            ctk.CTkLabel(parent, text="", width=80).grid(row=0, column=column, padx=(6, 6))
            return
        btn = ctk.CTkButton(
            parent,
            text="Editar obs.",
            width=80,
            height=22,
            font=ctk.CTkFont(size=11),
            command=command,
        )
        btn.grid(row=0, column=column, padx=(6, 6), pady=2)

    # ── Formateadores de celda ────────────────────────────────────────────

    @staticmethod
    def _formatear_hora(valor: Optional[str]) -> str:
        """``"08:00:00"`` → ``"08:00"``. ``None`` → ``"—"``."""
        if valor is None:
            return "—"
        # Las horas llegan como ``HH:MM:SS`` desde SQLite — recortamos
        # los segundos para no saturar la tabla.
        return valor[:5] if len(valor) >= 5 else valor

    @staticmethod
    def _formatear_observaciones(valor: Optional[str]) -> str:
        """Trunca a 40 caracteres + elipsis. ``None`` → ``""``."""
        if not valor:
            return ""
        if len(valor) > 40:
            return valor[:37] + "..."
        return valor

    @staticmethod
    def _formatear_estado(vista: AsistenciaVista, label_base: str) -> str:
        """Añade ``(+N min)`` al label si hay minutos de tarde/temprana."""
        minutos_tarde = vista.asistencia.minutos_tarde
        minutos_temprana = vista.asistencia.minutos_salida_temprana
        extras: List[str] = []
        if minutos_tarde > 0:
            extras.append(f"+{minutos_tarde}min tarde")
        if minutos_temprana > 0:
            extras.append(f"-{minutos_temprana}min salida")
        if not extras:
            return label_base
        return f"{label_base} ({', '.join(extras)})"

    # ── Handler del botón "Re-consolidar" ─────────────────────────────────

    def _on_re_consolidar(self) -> None:
        """Pide confirmación y dispara la re-consolidación del rango.

        Respeta el filtro de empleado activo en el combo: si hay un
        empleado seleccionado, solo se re-consolida ese empleado; si
        está en "(Todos los empleados)", se re-consolidan todos los
        activos del rango.
        """
        desde = self._date_desde.get_date().isoformat()
        hasta = self._date_hasta.get_date().isoformat()
        empleado_id = self._empleado_id_por_label.get(self._combo_empleado.get())
        label_combo = self._combo_empleado.get()

        if desde > hasta:
            messagebox.showwarning(
                "Rango inválido",
                "La fecha 'Desde' no puede ser posterior a 'Hasta'.",
            )
            return

        if not self._confirmar_re_consolidacion(desde, hasta, label_combo, empleado_id):
            return

        self._bloquear_re_consolidar()
        run_async_ui(
            self,
            work=lambda: self._controller.re_consolidar(
                desde=desde, hasta=hasta, empleado_id=empleado_id
            ),
            on_success=self._on_re_consolidar_ok,
            on_error=self._on_re_consolidar_err,
        )

    def _confirmar_re_consolidacion(
        self,
        desde: str,
        hasta: str,
        label_combo: str,
        empleado_id: Optional[int],
    ) -> bool:
        """Muestra un messagebox de confirmación antes de ejecutar."""
        alcance = (
            f"el empleado seleccionado ({label_combo})"
            if empleado_id is not None
            else "todos los empleados activos"
        )
        mensaje = (
            f"Se regenerarán las asistencias del {desde} al {hasta} "
            f"para {alcance}.\n\n"
            "Las observaciones manuales se conservan. "
            "¿Confirma la re-consolidación?"
        )
        return messagebox.askyesno("Confirmar re-consolidación", mensaje)

    def _bloquear_re_consolidar(self) -> None:
        """Deshabilita ambos botones y actualiza el status durante la operación."""
        self._btn_cargar.configure(state="disabled")
        if self._btn_re_consolidar is not None:
            self._btn_re_consolidar.configure(state="disabled", text="Re-consolidando...")
        self._spinner.grid(row=0, column=1, sticky="e", padx=12)
        self._spinner.start()
        self._status_label.configure(
            text="Re-consolidando asistencias...",
            text_color=("gray20", "gray80"),
        )

    def _desbloquear_re_consolidar(self) -> None:
        """Revierte el estado tras terminar la re-consolidación."""
        self._spinner.stop()
        self._spinner.grid_forget()
        self._btn_cargar.configure(state="normal")
        if self._btn_re_consolidar is not None:
            self._btn_re_consolidar.configure(state="normal", text="Re-consolidar")

    def _on_re_consolidar_ok(self, resultado: ResultadoConsolidacion) -> None:
        """Callback en UI thread: muestra resumen y recarga la tabla."""
        self._desbloquear_re_consolidar()
        self._status_label.configure(
            text=(
                f"Re-consolidación OK: {resultado.asistencias_upsertadas} "
                f"asistencia(s) procesada(s) en {resultado.dias_procesados} día(s)."
            ),
            text_color=("#1b5e20", "#a5d6a7"),
        )
        # Recargamos la tabla para que muestre los datos frescos.
        self._on_cargar()

    def _on_re_consolidar_err(self, exc: BaseException) -> None:
        """Callback en UI thread tras un fallo de la re-consolidación."""
        self._desbloquear_re_consolidar()
        self._log.exception("Error re-consolidando", exc_info=exc)
        titulo, mensaje = self._mensaje_error_re_consolidar(exc)
        self._status_label.configure(
            text="Error: no se pudo re-consolidar.",
            text_color=("#b71c1c", "#ef9a9a"),
        )
        messagebox.showerror(titulo, mensaje)

    @staticmethod
    def _mensaje_error_re_consolidar(exc: BaseException) -> Tuple[str, str]:
        """Traduce excepción de dominio a (título, mensaje) para el usuario."""
        if isinstance(exc, (InvalidRangoError, InvalidDateError)):
            return ("Rango inválido", str(exc))
        if isinstance(exc, EmpleadoNotFoundError):
            return (
                "Empleado no válido",
                "El empleado seleccionado no está activo. " "Recargue la lista de empleados.",
            )
        return (
            "Error al re-consolidar",
            "Ocurrió un error inesperado durante la re-consolidación. "
            "Revise el log para detalles.",
        )

    # ── Handler del botón "Editar obs." ───────────────────────────────────

    def _abrir_dialogo_observacion(self, vista: AsistenciaVista) -> None:
        """Abre el diálogo modal para editar la observación de la fila."""
        if vista.asistencia.id is None:
            # Defensivo: la BD siempre devuelve id, pero el tipo lo
            # permite opcional por el modelo.
            messagebox.showerror("Error", "La fila no tiene un id válido.")
            return
        asistencia_id = vista.asistencia.id
        subtitulo = f"{vista.asistencia.fecha} — {vista.empleado_nombre_completo}"
        ObservacionDialog(
            self,
            subtitle=subtitulo,
            on_submit=lambda texto: self._guardar_observacion(asistencia_id, texto),
            initial_text=vista.asistencia.observaciones,
        )

    def _guardar_observacion(self, asistencia_id: int, texto: Optional[str]) -> Optional[str]:
        """Callback del diálogo: llama al controller y recarga la lista.

        Returns:
            ``None`` si guardó OK (el diálogo se cierra), o un ``str``
            con el mensaje de error a mostrar in-dialog.
        """
        try:
            self._controller.update_observaciones(asistencia_id, texto)
        except AsistenciaNotFoundError:
            return "La fila ya no existe. Recargue la lista."
        except Exception:  # noqa: BLE001 — último recurso
            self._log.exception("Error guardando observación id=%s", asistencia_id)
            return "No se pudo guardar. Revise el log para detalles."
        # Éxito — recargamos el último rango para que la obs actualizada
        # aparezca sin que el usuario tenga que re-pulsar Cargar.
        self._recargar_si_aplica()
        return None

    def _recargar_si_aplica(self) -> None:
        """Re-dispara la última query si hay una en memoria.

        ``_ultimo_desde``/``_ultimo_hasta`` son strings ISO ``YYYY-MM-DD``
        (eso es lo que la vista guarda cuando se ejecuta una búsqueda
        desde ``_on_cargar``). ``tkcalendar.DateEntry.set_date`` exige
        ``date``/``datetime`` — pasarle el string crudo funciona en dev
        pero rompe en el bundle PyInstaller con
        ``AttributeError: 'str' object has no attribute 'year'``. Por
        eso convertimos explícitamente con ``date.fromisoformat``.
        """
        if self._ultimo_desde is None or self._ultimo_hasta is None:
            return
        # Simulamos el click en Cargar con los últimos parámetros. Esto
        # re-bloquea controles y re-pinta la tabla al completar.
        self._date_desde.set_date(date.fromisoformat(self._ultimo_desde))
        self._date_hasta.set_date(date.fromisoformat(self._ultimo_hasta))
        self._on_cargar()
