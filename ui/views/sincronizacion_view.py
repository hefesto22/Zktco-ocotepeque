"""Vista de Sincronización ZKTeco (Sub-3.4a).

Permite al operador (o rol superior) disparar una descarga de marcadas
desde un reloj ZKTeco y ver el resumen del resultado.

Diseño visual:

    ┌────────────────────────────────────────────────────────────┐
    │  Sincronización ZKTeco                                      │
    │  [banner aviso_recuperacion si aplica]                      │
    ├────────────────────────────────────────────────────────────┤
    │  Dispositivo:   [CTkComboBox ▼]                             │
    │  Desde:         [DateEntry 📅]                              │
    │  Hasta:         [DateEntry 📅]                              │
    │                              [  Sincronizar  ]              │
    ├────────────────────────────────────────────────────────────┤
    │  Estado: [spinner + label]                                  │
    │                                                             │
    │  Último resultado                                            │
    │    Registros recibidos: 123                                 │
    │    Asistencias consolidadas: 42 · Empleados: 15 · Días: 7   │
    │    ▸ Advertencias (3) — IDs no mapeados                     │
    └────────────────────────────────────────────────────────────┘

Threading:
    La llamada ``controller.ejecutar_sincronizacion`` puede tardar
    varios segundos (pull TCP + consolidación). Se envuelve en
    ``run_async_ui`` para que el UI thread siga fluido. Los callbacks
    de éxito/error vuelven al UI thread automáticamente.
"""

from __future__ import annotations

import logging
from datetime import date
from tkinter import messagebox
from typing import List, Optional

import customtkinter as ctk
from tkcalendar import DateEntry

from core.models.dispositivo import Dispositivo
from core.services.errors import (
    DispositivoInactiveError,
    DispositivoNotFoundError,
    InvalidDateError,
    InvalidRangoError,
)
from core.services.sincronizacion_result import (
    MarcadaDesconocida,
    ResultadoSincronizacion,
)
from infrastructure.zkteco.exceptions import (
    ZKAdapterError,
    ZKConnectionError,
    ZKProtocolError,
    ZKTimeoutError,
)
from ui.async_util import run_async_ui
from ui.controllers.sincronizacion_controller import SincronizacionController


# Formato legible en el combo: "Nombre — ip:puerto".
def _label_dispositivo(dispositivo: Dispositivo) -> str:
    """Formatea un dispositivo para el combo."""
    return f"{dispositivo.nombre} — {dispositivo.ip}:{dispositivo.puerto}"


class SincronizacionView(ctk.CTkFrame):
    """Vista raíz del módulo Sincronización ZKTeco."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        controller: SincronizacionController,
    ) -> None:
        """Construye la vista.

        Args:
            master: Contenedor Tk donde se monta.
            controller: Controller ya inyectado con session + servicios.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._controller = controller
        self._log = logging.getLogger(self.__class__.__name__)

        # Caché de dispositivos cargados (label → Dispositivo).
        self._dispositivos_por_label: dict[str, Dispositivo] = {}
        # Resultado de la última sincronización exitosa (para el toggle).
        self._ultimo_resultado: Optional[ResultadoSincronizacion] = None
        # Estado del toggle de advertencias.
        self._advertencias_expandidas = False

        self._construir_ui()
        self._cargar_dispositivos_iniciales()
        self._pintar_aviso_recuperacion()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma título, banner, form, status y resumen con grid."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        titulo = ctk.CTkLabel(
            self,
            text="Sincronización ZKTeco",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))

        # Banner de aviso_recuperacion (oculto al inicio).
        self._banner = ctk.CTkFrame(self, fg_color=("#fff3cd", "#4a3d10"))
        self._banner_label = ctk.CTkLabel(
            self._banner,
            text="",
            text_color=("#664d03", "#ffe69c"),
            anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self._banner_label.pack(padx=12, pady=8, fill="x")

        self._form = self._construir_form()
        self._form.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))

        self._status = self._construir_status()
        self._status.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))

        self._resumen = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._resumen.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._resumen.grid_columnconfigure(0, weight=1)
        self._pintar_resumen_vacio()

    def _construir_form(self) -> ctk.CTkFrame:
        """Arma el formulario con combo de dispositivo, fechas y botón."""
        form = ctk.CTkFrame(self, fg_color=("gray92", "gray18"))
        form.grid_columnconfigure(1, weight=1)

        lbl_disp = ctk.CTkLabel(form, text="Dispositivo:", anchor="w")
        lbl_disp.grid(row=0, column=0, sticky="w", padx=(12, 8), pady=8)
        self._combo_disp = ctk.CTkComboBox(
            form,
            values=["(sin dispositivos)"],
            command=self._on_dispositivo_cambiado,
            width=320,
            state="readonly",
        )
        self._combo_disp.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=8)

        self._date_desde = self._construir_date_row(form, row=1, label="Desde:")
        self._date_hasta = self._construir_date_row(form, row=2, label="Hasta:")

        self._btn_sync = ctk.CTkButton(
            form,
            text="Sincronizar",
            command=self._on_sincronizar,
            height=36,
            width=160,
        )
        self._btn_sync.grid(row=3, column=1, sticky="e", padx=12, pady=(4, 12))
        return form

    def _construir_date_row(
        self,
        parent: ctk.CTkFrame,
        row: int,
        label: str,
    ) -> DateEntry:
        """Arma una fila ``Label + DateEntry`` y devuelve el DateEntry."""
        lbl = ctk.CTkLabel(parent, text=label, anchor="w")
        lbl.grid(row=row, column=0, sticky="w", padx=(12, 8), pady=6)
        picker = DateEntry(
            parent,
            date_pattern="yyyy-mm-dd",
            width=18,
            background="darkblue",
            foreground="white",
            borderwidth=2,
        )
        picker.grid(row=row, column=1, sticky="w", padx=(0, 12), pady=6)
        return picker

    def _construir_status(self) -> ctk.CTkFrame:
        """Arma la barra de estado con label y spinner."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(1, weight=1)
        self._status_label = ctk.CTkLabel(
            bar,
            text="Listo.",
            anchor="w",
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=12),
        )
        self._status_label.grid(row=0, column=0, sticky="w", padx=12)
        self._spinner = ctk.CTkProgressBar(bar, mode="indeterminate", width=160)
        # El spinner se hace ``grid`` solo cuando está activo.
        return bar

    # ── Carga inicial ─────────────────────────────────────────────────────

    def _cargar_dispositivos_iniciales(self) -> None:
        """Pinta el combo con los dispositivos activos del sistema."""
        try:
            dispositivos = self._controller.list_dispositivos_activos()
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando dispositivos")
            messagebox.showerror(
                "Error",
                "No se pudo cargar la lista de dispositivos. Revise la "
                "configuración en el módulo correspondiente.",
            )
            return
        self._poblar_combo(dispositivos)

    def _poblar_combo(self, dispositivos: List[Dispositivo]) -> None:
        """Configura el combo y preselecciona el primero si existe."""
        if not dispositivos:
            self._combo_disp.configure(values=["(sin dispositivos activos)"])
            self._combo_disp.set("(sin dispositivos activos)")
            self._btn_sync.configure(state="disabled")
            return
        self._dispositivos_por_label = {_label_dispositivo(d): d for d in dispositivos}
        labels = list(self._dispositivos_por_label.keys())
        self._combo_disp.configure(values=labels)
        self._combo_disp.set(labels[0])
        self._btn_sync.configure(state="normal")
        self._on_dispositivo_cambiado(labels[0])

    # ── Banner de aviso_recuperacion ──────────────────────────────────────

    def _pintar_aviso_recuperacion(self) -> None:
        """Consume el aviso del controller y muestra el banner si aplica."""
        cantidad = self._controller.consumir_aviso_recuperacion()
        if not cantidad:
            return
        plural = "s" if cantidad != 1 else ""
        texto = (
            f"Se cerr\u00f3 {cantidad} sincronizaci\u00f3n{plural} que qued\u00f3 "
            f"interrumpida por un cierre previo de la aplicaci\u00f3n. "
            f"Puede reintentar si hace falta."
        )
        self._banner_label.configure(text=texto)
        self._banner.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_dispositivo_cambiado(self, label: str) -> None:
        """Recalcula el rango default cada vez que cambia el dispositivo.

        ``calcular_rango_default`` retorna strings ISO ``YYYY-MM-DD``;
        ``tkcalendar.DateEntry.set_date`` exige un ``date``/``datetime``
        — pasarle el string crudo funciona en dev (babel parsea por su
        cuenta) pero rompe en el bundle PyInstaller con
        ``AttributeError: 'str' object has no attribute 'year'``.
        Por eso convertimos explícitamente con ``date.fromisoformat``.
        """
        dispositivo = self._dispositivos_por_label.get(label)
        if dispositivo is None or dispositivo.id is None:
            return
        try:
            desde_iso, hasta_iso = self._controller.calcular_rango_default(dispositivo.id)
            self._date_desde.set_date(date.fromisoformat(desde_iso))
            self._date_hasta.set_date(date.fromisoformat(hasta_iso))
        except Exception:  # noqa: BLE001
            self._log.exception("Error calculando rango default dispositivo=%s", dispositivo.id)
            return

    def _on_sincronizar(self) -> None:
        """Dispara la sincronización en un thread, no bloquea el UI."""
        dispositivo = self._dispositivos_por_label.get(self._combo_disp.get())
        if dispositivo is None or dispositivo.id is None:
            return
        desde = self._date_desde.get_date().isoformat()
        hasta = self._date_hasta.get_date().isoformat()
        self._bloquear_controles_sync()
        run_async_ui(
            self,
            work=lambda: self._controller.ejecutar_sincronizacion(
                dispositivo_id=dispositivo.id,  # type: ignore[arg-type]
                rango_desde=desde,
                rango_hasta=hasta,
            ),
            on_success=self._on_sync_ok,
            on_error=self._on_sync_err,
        )

    def _bloquear_controles_sync(self) -> None:
        """Deshabilita el botón y activa el spinner mientras corre la sync."""
        self._btn_sync.configure(state="disabled", text="Sincronizando...")
        self._spinner.grid(row=0, column=1, sticky="e", padx=12)
        self._spinner.start()
        self._status_label.configure(
            text="Descargando marcadas del reloj...",
            text_color=("gray20", "gray80"),
        )

    def _desbloquear_controles_sync(self) -> None:
        """Revierte el estado del formulario tras terminar la sync."""
        self._spinner.stop()
        self._spinner.grid_forget()
        self._btn_sync.configure(state="normal", text="Sincronizar")

    def _on_sync_ok(self, resultado: ResultadoSincronizacion) -> None:
        """Callback en UI thread tras una sincronización exitosa."""
        self._desbloquear_controles_sync()
        self._ultimo_resultado = resultado
        self._status_label.configure(
            text="Sincronización completada.",
            text_color=("#1b5e20", "#a5d6a7"),
        )
        self._pintar_resumen(resultado)

    def _on_sync_err(self, exc: BaseException) -> None:
        """Callback en UI thread tras un fallo de la sincronización."""
        self._desbloquear_controles_sync()
        mensaje = self._traducir_error(exc)
        self._log.exception("Error en sincronización", exc_info=exc)
        self._status_label.configure(
            text=f"Error: {mensaje}",
            text_color=("#b71c1c", "#ef9a9a"),
        )
        messagebox.showerror("Sincronización fallida", mensaje)

    # ── Render del resumen ────────────────────────────────────────────────

    def _pintar_resumen_vacio(self) -> None:
        """Placeholder del área de resumen cuando aún no hay sync en memoria."""
        for hijo in self._resumen.winfo_children():
            hijo.destroy()
        msg = ctk.CTkLabel(
            self._resumen,
            text=(
                "Aún no se ha ejecutado ninguna sincronización en esta "
                "sesión. Seleccione un dispositivo, un rango de fechas y "
                "presione Sincronizar."
            ),
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=700,
            justify="left",
            anchor="w",
        )
        msg.grid(row=0, column=0, sticky="w", padx=16, pady=16)

    def _pintar_resumen(self, resultado: ResultadoSincronizacion) -> None:
        """Re-pinta el área de resumen con los datos del último resultado."""
        for hijo in self._resumen.winfo_children():
            hijo.destroy()

        titulo = ctk.CTkLabel(
            self._resumen,
            text="Último resultado",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        titulo.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        registros = ctk.CTkLabel(
            self._resumen,
            text=f"Registros recibidos: {resultado.registros_recibidos}",
            anchor="w",
        )
        registros.grid(row=1, column=0, sticky="ew", padx=12, pady=2)

        self._pintar_linea_consolidacion(resultado, start_row=2)
        self._pintar_advertencias(resultado, start_row=4)

    def _pintar_linea_consolidacion(
        self,
        resultado: ResultadoSincronizacion,
        start_row: int,
    ) -> None:
        """Línea con el resumen de la consolidación in-line."""
        if resultado.error_consolidacion is not None:
            aviso = ctk.CTkLabel(
                self._resumen,
                text=(
                    "La descarga fue exitosa pero la consolidación falló: "
                    f"{resultado.error_consolidacion}"
                ),
                text_color=("#b71c1c", "#ef9a9a"),
                wraplength=700,
                justify="left",
                anchor="w",
            )
            aviso.grid(row=start_row, column=0, sticky="ew", padx=12, pady=2)
            return
        cons = resultado.consolidacion
        if cons is None:
            return
        texto = (
            f"Asistencias consolidadas: {cons.asistencias_upsertadas}  ·  "
            f"Empleados: {cons.empleados_procesados}  ·  "
            f"Días: {cons.dias_procesados}"
        )
        lbl = ctk.CTkLabel(self._resumen, text=texto, anchor="w")
        lbl.grid(row=start_row, column=0, sticky="ew", padx=12, pady=2)

    def _pintar_advertencias(
        self,
        resultado: ResultadoSincronizacion,
        start_row: int,
    ) -> None:
        """Botón toggle + detalle de marcadas desconocidas, si hay."""
        cons = resultado.consolidacion
        if cons is None or not cons.marcadas_desconocidas:
            return
        cantidad = len(cons.marcadas_desconocidas)
        flecha = "▾" if self._advertencias_expandidas else "▸"
        self._btn_toggle = ctk.CTkButton(
            self._resumen,
            text=f"{flecha} Advertencias ({cantidad}) — IDs no mapeados",
            anchor="w",
            fg_color="transparent",
            text_color=("#9a6200", "#ffc862"),
            hover_color=("gray85", "gray25"),
            command=self._on_toggle_advertencias,
        )
        self._btn_toggle.grid(row=start_row, column=0, sticky="ew", padx=12, pady=(8, 2))
        if self._advertencias_expandidas:
            self._pintar_detalle_advertencias(cons.marcadas_desconocidas, row=start_row + 1)

    def _pintar_detalle_advertencias(
        self,
        marcadas: List[MarcadaDesconocida],
        row: int,
    ) -> None:
        """Lista cada ``zkteco_user_id`` desconocido y su cantidad de marcadas."""
        contenedor = ctk.CTkFrame(self._resumen, fg_color=("gray90", "gray22"))
        contenedor.grid(row=row, column=0, sticky="ew", padx=24, pady=(0, 8))
        contenedor.grid_columnconfigure(0, weight=1)
        intro = ctk.CTkLabel(
            contenedor,
            text=(
                "Estos IDs llegaron del reloj pero no están asignados a "
                "ningún empleado. Revise el módulo de Empleados para "
                "mapearlos."
            ),
            text_color=("gray30", "gray70"),
            wraplength=650,
            justify="left",
            anchor="w",
        )
        intro.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        for idx, md in enumerate(marcadas, start=1):
            texto = f"ZKTeco ID {md.zkteco_user_id}  ·  " f"{md.cantidad_marcadas} marcada(s)"
            fila = ctk.CTkLabel(contenedor, text=texto, anchor="w")
            fila.grid(row=idx, column=0, sticky="ew", padx=8, pady=1)

    def _on_toggle_advertencias(self) -> None:
        """Expande o colapsa el detalle de advertencias."""
        self._advertencias_expandidas = not self._advertencias_expandidas
        if self._ultimo_resultado is not None:
            self._pintar_resumen(self._ultimo_resultado)

    # ── Traducción de errores a mensajes en español ───────────────────────

    @staticmethod
    def _traducir_error(exc: BaseException) -> str:
        """Convierte una excepción conocida en un mensaje legible."""
        if isinstance(exc, ZKConnectionError):
            return (
                "No se pudo conectar al reloj. Verifique que esté encendido "
                "y que la IP/puerto sean correctos."
            )
        if isinstance(exc, ZKTimeoutError):
            return (
                "El reloj dejó de responder. Intente de nuevo; si persiste, "
                "revise la red o reinicie el dispositivo."
            )
        if isinstance(exc, ZKProtocolError):
            return (
                "El reloj respondió con un formato inesperado. Verifique "
                "la versión del firmware."
            )
        if isinstance(exc, ZKAdapterError):
            return f"Error del reloj: {exc}"
        if isinstance(
            exc,
            (
                DispositivoNotFoundError,
                DispositivoInactiveError,
                InvalidRangoError,
                InvalidDateError,
            ),
        ):
            return str(exc)
        return "Ocurrió un error inesperado. Consulte el log para detalles."
