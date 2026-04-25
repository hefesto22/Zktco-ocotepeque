"""Vista de Reportes (Sub-3.5b).

Permite a roles con ``EXPORT_REPORTS`` (SUPERADMIN, ADMIN, REPORTES)
generar archivos Excel del reporte de asistencia y consultar el
historial de descargas si además poseen ``VIEW_EXPORT_HISTORY``.

Diseño visual:

    ┌────────────────────────────────────────────────────────────┐
    │  Reportes                                                  │
    ├────────────────────────────────────────────────────────────┤
    │  ▣ Exportar  │  Historial de descargas                     │
    ├────────────────────────────────────────────────────────────┤
    │  Empleado: [CTkComboBox ▼]   Desde: [📅]   Hasta: [📅]      │
    │                                       [ Exportar Excel ]   │
    │  Estado: [spinner + label]                                 │
    └────────────────────────────────────────────────────────────┘

Decisiones aprobadas en Sub-3.5b:
    - Vista única con CTkTabview interno (no dos entradas de menú).
    - Selectores de fecha con ``tkcalendar.DateEntry`` (mismo patrón
      que ``AsistenciaView`` / ``SincronizacionView``).
    - Filtro de empleado con CTkComboBox y "(Todos los empleados)".
    - Pestaña de Historial solo si la sesión tiene
      ``VIEW_EXPORT_HISTORY`` — el render usa CTkScrollableFrame con
      filas custom (consistente con AsistenciaView).
    - Confirmación previa al export si el conteo estimado es >= 1000.

Threading:
    El conteo y el export tocan SQLite y openpyxl — pueden tardar
    varios segundos. Se envuelven en ``run_async_ui`` para que la UI
    quede responsiva con un spinner.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from tkinter import filedialog, messagebox
from typing import List, Optional, Tuple

import customtkinter as ctk
from tkcalendar import DateEntry

from core.models import permissions as perms
from core.models.descarga_reporte import DescargaReporte
from core.services.errors import (
    InvalidDateError,
    InvalidRangoError,
    ReporteIOError,
    ReporteSinDatosError,
)
from ui.async_util import run_async_ui
from ui.controllers.reporte_controller import ReporteController


# Sentinel para "todos los empleados" en el combo de filtro.
_LABEL_TODOS: str = "(Todos los empleados)"

# Rango default del export: del primer día del mes en curso hasta hoy.
# Es el caso más frecuente del cierre quincenal/mensual de planilla.
_DIA_INICIO_MES_DEFAULT: int = 1

# Si el conteo estimado supera este umbral, pedimos confirmación
# explícita antes de generar el archivo (decisión Sub-3.5b: 1000 filas).
_UMBRAL_CONFIRMACION: int = 1000

# Límite del historial mostrado en la pestaña — el repo soporta más,
# pero 50 filas cubren ~6 meses de uso típico sin saturar el scroll.
_LIMITE_HISTORIAL: int = 50

# Anchos de columna (caracteres) del historial. Se calculan a 7 px por
# carácter para alinear con AsistenciaView; ajusta si cambia el font.
_COL_FECHA_W: int = 19
_COL_TIPO_W: int = 12
_COL_RANGO_W: int = 24
_COL_FILTRO_W: int = 8
_COL_FILAS_W: int = 8
_COL_RUTA_W: int = 38


class ReportesView(ctk.CTkFrame):
    """Vista raíz del módulo Reportes (export + historial)."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: ReporteController,
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
        self._mostrar_historial = self._sesion_puede_ver_historial()

        self._construir_ui()
        self._cargar_empleados_combo()
        if self._mostrar_historial:
            self._refrescar_historial()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _sesion_puede_ver_historial(self) -> bool:
        """¿La sesión activa tiene VIEW_EXPORT_HISTORY?"""
        return self._controller.session.has_permission(perms.VIEW_EXPORT_HISTORY)

    def _construir_ui(self) -> None:
        """Arma título + tabview con pestañas Exportar / Historial."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Reportes",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))

        self._tabs = ctk.CTkTabview(self, corner_radius=8)
        self._tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self._tabs.add("Exportar")
        self._construir_tab_exportar(self._tabs.tab("Exportar"))

        if self._mostrar_historial:
            self._tabs.add("Historial de descargas")
            self._construir_tab_historial(self._tabs.tab("Historial de descargas"))

    # ── Tab Exportar ──────────────────────────────────────────────────────

    def _construir_tab_exportar(self, parent: ctk.CTkFrame) -> None:
        """Pinta el form de export + barra de estado dentro del tab."""
        parent.grid_columnconfigure(0, weight=1)

        self._form_exp = self._construir_form_exportar(parent)
        self._form_exp.grid(row=0, column=0, sticky="ew", padx=8, pady=(12, 8))

        self._status_exp = self._construir_status_exportar(parent)
        self._status_exp.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))

        ayuda = ctk.CTkLabel(
            parent,
            text=(
                "Selecciona un rango de fechas (por defecto, del 1 del "
                "mes a hoy) y opcionalmente un empleado. El archivo "
                "Excel incluye una hoja de Resumen, una de Detalle y "
                "una de Metadatos para auditoría."
            ),
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=720,
            justify="left",
            anchor="w",
        )
        ayuda.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _construir_form_exportar(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        """Form con combo empleado, rango de fechas y botón Exportar."""
        form = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"))
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

        self._btn_exportar = ctk.CTkButton(
            form,
            text="Exportar Excel",
            command=self._on_exportar,
            height=32,
            width=160,
        )
        self._btn_exportar.grid(row=0, column=6, sticky="e", padx=(12, 12), pady=pad_y)

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
        """Setea los DateEntry al rango "desde el 1 del mes hasta hoy"."""
        hoy = date.today()
        primer_dia = hoy.replace(day=_DIA_INICIO_MES_DEFAULT)
        self._date_desde.set_date(primer_dia)
        self._date_hasta.set_date(hoy)

    def _construir_status_exportar(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        """Barra de estado del tab Exportar con label + spinner."""
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid_columnconfigure(1, weight=1)
        self._status_exp_label = ctk.CTkLabel(
            bar,
            text="Listo para exportar.",
            anchor="w",
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=12),
        )
        self._status_exp_label.grid(row=0, column=0, sticky="w", padx=12)
        self._spinner_exp = ctk.CTkProgressBar(bar, mode="indeterminate", width=160)
        return bar

    # ── Carga inicial del combo de empleados ──────────────────────────────

    def _cargar_empleados_combo(self) -> None:
        """Llama al controller y pobla el combo con los empleados activos."""
        try:
            tuplas = self._controller.list_empleados_para_filtro()
        except Exception:  # noqa: BLE001 — último recurso para no tumbar la UI
            self._log.exception("Error listando empleados para filtro de reportes")
            messagebox.showerror(
                "Error",
                "No se pudo cargar la lista de empleados. Intente de "
                "nuevo o consulte con el administrador.",
            )
            return
        self._poblar_combo_empleados(tuplas)

    def _poblar_combo_empleados(self, tuplas: List[Tuple[int, str]]) -> None:
        """Configura el combo. ``_LABEL_TODOS`` siempre va primero."""
        self._empleado_id_por_label = {_LABEL_TODOS: None}
        for emp_id, nombre in tuplas:
            label = nombre if nombre not in self._empleado_id_por_label else f"{nombre} (#{emp_id})"
            self._empleado_id_por_label[label] = emp_id
        self._combo_empleado.configure(values=list(self._empleado_id_por_label.keys()))
        self._combo_empleado.set(_LABEL_TODOS)

    # ── Handler del botón Exportar ────────────────────────────────────────

    def _on_exportar(self) -> None:
        """Coordina el flujo: validar → confirmar → escoger ruta → exportar."""
        desde = self._date_desde.get_date().isoformat()
        hasta = self._date_hasta.get_date().isoformat()
        empleado_id = self._empleado_id_por_label.get(self._combo_empleado.get())

        if desde > hasta:
            messagebox.showwarning(
                "Rango inválido",
                "La fecha 'Desde' no puede ser posterior a 'Hasta'.",
            )
            return

        if not self._confirmar_volumen_si_aplica(desde, hasta, empleado_id):
            return

        ruta = self._pedir_ruta_destino(desde, hasta, empleado_id)
        if ruta is None:
            return  # Usuario canceló el diálogo

        self._lanzar_export_async(desde, hasta, empleado_id, ruta)

    def _confirmar_volumen_si_aplica(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
    ) -> bool:
        """Si el conteo supera el umbral, pide confirmación al usuario.

        El conteo es síncrono — debería ser rápido (un ``COUNT(*)`` con
        índices). Si fallara, traducimos el error igual que el export
        para que el usuario vea un mensaje claro y NO continúe.
        """
        try:
            cantidad = self._controller.contar_filas_asistencia(
                desde=desde, hasta=hasta, empleado_id=empleado_id
            )
        except (InvalidRangoError, InvalidDateError) as exc:
            messagebox.showwarning("Rango inválido", str(exc))
            return False
        except Exception:  # noqa: BLE001 — defensivo: nunca tumbar la UI
            self._log.exception("Error contando filas para reporte")
            messagebox.showerror(
                "Error",
                "No se pudo calcular el tamaño del reporte. Intente de "
                "nuevo; si persiste, revise el log.",
            )
            return False
        if cantidad < _UMBRAL_CONFIRMACION:
            return True
        mensaje = (
            f"El reporte cubrirá {cantidad} fila(s) de asistencia. "
            "Generar el archivo puede tardar varios segundos. "
            "¿Continuar?"
        )
        return messagebox.askyesno("Confirmar exportación", mensaje)

    def _pedir_ruta_destino(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
    ) -> Optional[str]:
        """Abre el diálogo nativo "Guardar como"; devuelve None si cancela."""
        sufijo_emp = f"_emp{empleado_id}" if empleado_id is not None else ""
        nombre_default = f"reporte_asistencia_{desde}_a_{hasta}{sufijo_emp}.xlsx"
        ruta = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar reporte como",
            defaultextension=".xlsx",
            initialfile=nombre_default,
            filetypes=[("Excel (.xlsx)", "*.xlsx")],
        )
        # filedialog devuelve "" cuando el usuario cancela.
        if not ruta:
            return None
        return ruta

    def _lanzar_export_async(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
        output_path: str,
    ) -> None:
        """Ejecuta el export en thread daemon con spinner."""
        self._bloquear_export()
        run_async_ui(
            self,
            work=lambda: self._controller.exportar_asistencia(
                desde=desde,
                hasta=hasta,
                output_path=output_path,
                empleado_id=empleado_id,
            ),
            on_success=self._on_export_ok,
            on_error=self._on_export_err,
        )

    def _bloquear_export(self) -> None:
        """Deshabilita el botón y muestra el spinner durante el export."""
        self._btn_exportar.configure(state="disabled", text="Exportando...")
        self._spinner_exp.grid(row=0, column=1, sticky="e", padx=12)
        self._spinner_exp.start()
        self._status_exp_label.configure(
            text="Generando archivo Excel...",
            text_color=("gray20", "gray80"),
        )

    def _desbloquear_export(self) -> None:
        """Revierte el estado del form tras terminar (éxito o error)."""
        self._spinner_exp.stop()
        self._spinner_exp.grid_forget()
        self._btn_exportar.configure(state="normal", text="Exportar Excel")

    def _on_export_ok(self, descarga: DescargaReporte) -> None:
        """Callback en UI thread tras un export exitoso."""
        self._desbloquear_export()
        self._status_exp_label.configure(
            text=(
                f"Reporte generado: {descarga.filas_exportadas} fila(s) en "
                f"{os.path.basename(descarga.ruta_archivo)}."
            ),
            text_color=("#1b5e20", "#a5d6a7"),
        )
        self._refrescar_historial_si_aplica()
        self._mostrar_dialogo_post_export(descarga)

    def _on_export_err(self, exc: BaseException) -> None:
        """Callback en UI thread tras un fallo de export."""
        self._desbloquear_export()
        self._log.exception("Error exportando reporte", exc_info=exc)
        titulo, mensaje = self._mensaje_error_export(exc)
        self._status_exp_label.configure(
            text="Error: no se pudo generar el reporte.",
            text_color=("#b71c1c", "#ef9a9a"),
        )
        messagebox.showerror(titulo, mensaje)

    @staticmethod
    def _mensaje_error_export(exc: BaseException) -> Tuple[str, str]:
        """Traduce excepción de dominio a (título, mensaje) en español."""
        if isinstance(exc, ReporteSinDatosError):
            return ("Sin datos", str(exc))
        if isinstance(exc, ReporteIOError):
            return ("No se pudo guardar el archivo", str(exc))
        if isinstance(exc, (InvalidRangoError, InvalidDateError)):
            return ("Rango inválido", str(exc))
        return (
            "Error al exportar",
            "Ocurrió un error inesperado al generar el reporte. " "Revise el log para detalles.",
        )

    def _mostrar_dialogo_post_export(self, descarga: DescargaReporte) -> None:
        """Tras el éxito, ofrece abrir la carpeta destino."""
        respuesta = messagebox.askyesno(
            "Reporte generado",
            (
                f"Se generó el archivo con {descarga.filas_exportadas} "
                f"fila(s) en:\n\n{descarga.ruta_archivo}\n\n"
                "¿Desea abrir la carpeta donde se guardó?"
            ),
        )
        if not respuesta:
            return
        self._abrir_carpeta(descarga.ruta_archivo)

    def _abrir_carpeta(self, ruta_archivo: str) -> None:
        """Abre el explorador en la carpeta del archivo (multi-plataforma).

        Diseño: el target real es Windows (``os.startfile``); en macOS y
        Linux usamos ``open`` / ``xdg-open`` como fallback para que el
        flujo funcione también en desarrollo.
        """
        carpeta = os.path.dirname(ruta_archivo) or os.getcwd()
        try:
            if hasattr(os, "startfile"):
                # Windows — abre la carpeta en el Explorador.
                os.startfile(carpeta)
                return
            import subprocess
            import sys

            cmd = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([cmd, carpeta])  # noqa: S603 — ruta controlada
        except OSError:
            self._log.exception("No se pudo abrir la carpeta %s", carpeta)
            messagebox.showwarning(
                "No se pudo abrir la carpeta",
                "El archivo se generó correctamente, pero no se pudo "
                "abrir la carpeta automáticamente.",
            )

    # ── Tab Historial ─────────────────────────────────────────────────────

    def _construir_tab_historial(self, parent: ctk.CTkFrame) -> None:
        """Pinta header del historial + área scrolleable."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text=f"Últimas {_LIMITE_HISTORIAL} descargas",
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=4)
        self._btn_refrescar = ctk.CTkButton(
            header,
            text="Refrescar",
            command=self._refrescar_historial,
            width=110,
            height=28,
        )
        self._btn_refrescar.grid(row=0, column=1, sticky="e", padx=4)

        self._historial_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color=("gray95", "gray15"),
        )
        self._historial_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self._historial_frame.grid_columnconfigure(0, weight=1)

    def _refrescar_historial_si_aplica(self) -> None:
        """Recarga el historial si la pestaña está visible."""
        if self._mostrar_historial:
            self._refrescar_historial()

    def _refrescar_historial(self) -> None:
        """Recarga el historial desde el controller (sin spinner — es rápido)."""
        try:
            descargas = self._controller.list_historial_descargas(limit=_LIMITE_HISTORIAL)
        except Exception:  # noqa: BLE001 — defensivo
            self._log.exception("Error cargando historial de descargas")
            messagebox.showerror(
                "Error",
                "No se pudo cargar el historial de descargas. " "Revise el log para detalles.",
            )
            return
        self._pintar_historial(descargas)

    def _pintar_historial(self, descargas: List[DescargaReporte]) -> None:
        """Re-pinta el área del historial con las descargas dadas."""
        for hijo in self._historial_frame.winfo_children():
            hijo.destroy()

        if not descargas:
            self._pintar_historial_vacio()
            return

        self._pintar_cabecera_historial()
        for idx, descarga in enumerate(descargas, start=1):
            self._pintar_fila_historial(idx, descarga)

    def _pintar_historial_vacio(self) -> None:
        """Mensaje cuando no hay descargas registradas todavía."""
        msg = ctk.CTkLabel(
            self._historial_frame,
            text=(
                "No hay descargas registradas. Genere un reporte desde "
                "la pestaña Exportar para ver el historial."
            ),
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=700,
            justify="left",
            anchor="w",
        )
        msg.grid(row=0, column=0, sticky="w", padx=16, pady=16)

    def _pintar_cabecera_historial(self) -> None:
        """Cabecera con los nombres de las columnas."""
        cabecera = ctk.CTkFrame(self._historial_frame, fg_color=("gray85", "gray25"))
        cabecera.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        self._pintar_columnas_historial(
            cabecera,
            fecha="Fecha",
            tipo="Tipo",
            rango="Rango",
            filtro="Filtro",
            filas="Filas",
            ruta="Archivo",
            bold=True,
        )

    def _pintar_fila_historial(self, idx: int, descarga: DescargaReporte) -> None:
        """Pinta una fila de detalle del historial."""
        fila = ctk.CTkFrame(self._historial_frame, fg_color="transparent")
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=1)
        self._pintar_columnas_historial(
            fila,
            fecha=self._formatear_fecha_hora(descarga.fecha_hora_utc),
            tipo=descarga.tipo_reporte,
            rango=f"{descarga.rango_desde} → {descarga.rango_hasta}",
            filtro=self._formatear_filtro(descarga.empleado_id_filtro),
            filas=str(descarga.filas_exportadas),
            ruta=self._formatear_ruta(descarga.ruta_archivo),
            bold=False,
        )

    def _pintar_columnas_historial(  # noqa: PLR0913 — celdas de una fila tabular
        self,
        parent: ctk.CTkFrame,
        fecha: str,
        tipo: str,
        rango: str,
        filtro: str,
        filas: str,
        ruta: str,
        bold: bool,
    ) -> None:
        """Pinta las 6 columnas de una fila (datos o cabecera)."""
        parent.grid_columnconfigure(5, weight=1)
        font = ctk.CTkFont(size=12, weight="bold") if bold else ctk.CTkFont(size=12)
        self._celda_historial(parent, fecha, column=0, width=_COL_FECHA_W, font=font)
        self._celda_historial(parent, tipo, column=1, width=_COL_TIPO_W, font=font)
        self._celda_historial(parent, rango, column=2, width=_COL_RANGO_W, font=font)
        self._celda_historial(parent, filtro, column=3, width=_COL_FILTRO_W, font=font)
        self._celda_historial(parent, filas, column=4, width=_COL_FILAS_W, font=font)
        self._celda_historial(parent, ruta, column=5, width=_COL_RUTA_W, font=font)

    @staticmethod
    def _celda_historial(
        parent: ctk.CTkFrame,
        texto: str,
        column: int,
        width: int,
        font: ctk.CTkFont,
    ) -> None:
        """Pinta una celda de texto con ancho fijo."""
        lbl = ctk.CTkLabel(parent, text=texto, anchor="w", width=width * 7, font=font)
        lbl.grid(row=0, column=column, sticky="w", padx=(6, 4), pady=3)

    # ── Formateadores del historial ───────────────────────────────────────

    @staticmethod
    def _formatear_fecha_hora(iso_utc: str) -> str:
        """``"2026-04-24T10:00:00+00:00"`` → ``"2026-04-24 10:00"``."""
        # No parsamos para evitar ImportError si el ISO viene exótico:
        # cortamos por ``T`` y recortamos los segundos. Es un display
        # tabular — la auditoría completa queda en el campo persistido.
        if "T" not in iso_utc:
            return iso_utc
        fecha, resto = iso_utc.split("T", 1)
        hora = resto[:5] if len(resto) >= 5 else resto
        return f"{fecha} {hora}"

    @staticmethod
    def _formatear_filtro(empleado_id: Optional[int]) -> str:
        """``None`` → ``"Todos"``; ``42`` → ``"#42"``."""
        if empleado_id is None:
            return "Todos"
        return f"#{empleado_id}"

    @staticmethod
    def _formatear_ruta(ruta: str) -> str:
        """Trunca rutas largas dejando solo el nombre de archivo si supera el ancho."""
        nombre = os.path.basename(ruta)
        if len(nombre) > _COL_RUTA_W:
            return nombre[: _COL_RUTA_W - 3] + "..."
        return nombre
