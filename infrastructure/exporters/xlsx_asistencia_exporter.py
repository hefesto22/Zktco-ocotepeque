"""Implementación xlsx de ``IAsistenciaExporter`` con openpyxl.

Layout multi-hoja decidido en Sub-3.5 (decisión B3):

    1. ``Resumen``    — una fila por empleado con conteos y minutos.
    2. ``Detalle``    — una fila por (empleado, fecha) con todo el
                        consolidado del día.
    3. ``Metadatos``  — rango, filtro, actor, timestamp.

Decisiones de formato:
    - Header de cada hoja en negrita.
    - ``freeze_panes = "A2"`` para que el header quede fijo al hacer scroll.
    - Auto-width: medimos la cadena más larga por columna y ajustamos el
      ``column_dimensions[letter].width`` con un padding fijo.
    - Sin formato condicional ni colores — el archivo está pensado para
      consumirse en Excel y luego adaptarlo si la municipalidad quiere.

I/O:
    Cualquier ``OSError`` durante ``wb.save`` (permisos, disco lleno,
    archivo abierto en Excel) se propaga sin envolver. El servicio de
    reportes la traduce a ``ReporteIOError`` con mensaje en español.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from core.services.asistencia_exporter import (
    DatosReporteAsistencia,
    IAsistenciaExporter,
    ResumenEmpleado,
)
from core.services.asistencia_service import AsistenciaVista

# Padding aplicado al ancho calculado por contenido. Compensa el padding
# visual interno de las celdas y deja una columna respirable.
_AUTO_WIDTH_PADDING: int = 2
_AUTO_WIDTH_MIN: int = 10
_AUTO_WIDTH_MAX: int = 60


class XlsxAsistenciaExporter(IAsistenciaExporter):
    """Exporta un reporte de asistencia a un archivo .xlsx multi-hoja."""

    def exportar(self, datos: DatosReporteAsistencia, output_path: str) -> int:
        """Genera el archivo y devuelve la cantidad de filas de detalle."""
        wb = Workbook()
        # ``Workbook()`` crea una hoja default — la reutilizamos como Resumen
        # en vez de crear una nueva y borrar la default (evita warnings).
        sheet_resumen = wb.active
        if sheet_resumen is None:  # defensa: openpyxl siempre crea la hoja
            sheet_resumen = wb.create_sheet("Resumen")
        sheet_resumen.title = "Resumen"
        _llenar_resumen(sheet_resumen, datos.resumen_por_empleado)

        sheet_detalle = wb.create_sheet("Detalle")
        _llenar_detalle(sheet_detalle, datos.items)

        sheet_metadatos = wb.create_sheet("Metadatos")
        _llenar_metadatos(sheet_metadatos, datos)

        wb.save(output_path)
        return len(datos.items)


# ── Hoja: Resumen ────────────────────────────────────────────────────────────


_HEADERS_RESUMEN: Tuple[str, ...] = (
    "Empleado",
    "DNI",
    "Días presente",
    "Días tarde",
    "Días salida temprana",
    "Días ausente",
    "Días feriado",
    "Días sin turno",
    "Días incompleto",
    "Min. tarde (total)",
    "Min. salida temprana (total)",
)


def _llenar_resumen(ws: Worksheet, resumen: Sequence[ResumenEmpleado]) -> None:
    """Pinta la hoja Resumen con header + una fila por empleado."""
    _escribir_header(ws, _HEADERS_RESUMEN)
    for fila_idx, item in enumerate(resumen, start=2):
        ws.cell(row=fila_idx, column=1, value=item.empleado_nombre)
        ws.cell(row=fila_idx, column=2, value=item.empleado_dni or "")
        ws.cell(row=fila_idx, column=3, value=item.dias_presente)
        ws.cell(row=fila_idx, column=4, value=item.dias_tarde)
        ws.cell(row=fila_idx, column=5, value=item.dias_salida_temprana)
        ws.cell(row=fila_idx, column=6, value=item.dias_ausente)
        ws.cell(row=fila_idx, column=7, value=item.dias_feriado)
        ws.cell(row=fila_idx, column=8, value=item.dias_sin_turno)
        ws.cell(row=fila_idx, column=9, value=item.dias_incompleto)
        ws.cell(row=fila_idx, column=10, value=item.total_minutos_tarde)
        ws.cell(row=fila_idx, column=11, value=item.total_minutos_salida_temprana)
    _ajustar_anchos(ws, _HEADERS_RESUMEN)


# ── Hoja: Detalle ────────────────────────────────────────────────────────────


_HEADERS_DETALLE: Tuple[str, ...] = (
    "Fecha",
    "Empleado",
    "DNI",
    "Turno",
    "Estado",
    "Hora entrada real",
    "Hora salida real",
    "Min. tarde",
    "Min. salida temprana",
    "Observaciones",
)


def _llenar_detalle(ws: Worksheet, items: Sequence[AsistenciaVista]) -> None:
    """Pinta la hoja Detalle con header + una fila por (empleado, fecha)."""
    _escribir_header(ws, _HEADERS_DETALLE)
    for fila_idx, vista in enumerate(items, start=2):
        a = vista.asistencia
        ws.cell(row=fila_idx, column=1, value=a.fecha)
        ws.cell(row=fila_idx, column=2, value=vista.empleado_nombre_completo)
        ws.cell(row=fila_idx, column=3, value=vista.empleado_dni or "")
        ws.cell(row=fila_idx, column=4, value=vista.turno_nombre or "")
        ws.cell(row=fila_idx, column=5, value=a.estado)
        ws.cell(row=fila_idx, column=6, value=a.hora_entrada_real or "")
        ws.cell(row=fila_idx, column=7, value=a.hora_salida_real or "")
        ws.cell(row=fila_idx, column=8, value=a.minutos_tarde)
        ws.cell(row=fila_idx, column=9, value=a.minutos_salida_temprana)
        ws.cell(row=fila_idx, column=10, value=a.observaciones or "")
    _ajustar_anchos(ws, _HEADERS_DETALLE)


# ── Hoja: Metadatos ──────────────────────────────────────────────────────────


def _llenar_metadatos(ws: Worksheet, datos: DatosReporteAsistencia) -> None:
    """Pinta la hoja Metadatos como tabla clave/valor de 2 columnas."""
    filas: List[Tuple[str, str]] = [
        ("Reporte", "Asistencia"),
        ("Rango desde", datos.rango_desde),
        ("Rango hasta", datos.rango_hasta),
        (
            "Filtro de empleado",
            (
                datos.empleado_nombre_filtro
                if datos.empleado_nombre_filtro is not None
                else "(todos los empleados)"
            ),
        ),
        ("Generado por", datos.actor_username),
        ("Generado el (UTC)", datos.timestamp_iso),
        ("Filas de detalle", str(len(datos.items))),
        ("Empleados en resumen", str(len(datos.resumen_por_empleado))),
    ]
    headers = ("Campo", "Valor")
    _escribir_header(ws, headers)
    for fila_idx, (campo, valor) in enumerate(filas, start=2):
        ws.cell(row=fila_idx, column=1, value=campo)
        ws.cell(row=fila_idx, column=2, value=valor)
    _ajustar_anchos(ws, headers, filas_extra=filas)


# ── Helpers de formato ───────────────────────────────────────────────────────


def _escribir_header(ws: Worksheet, headers: Sequence[str]) -> None:
    """Escribe la fila 1 con los headers en negrita y congela el panel."""
    for col_idx, label in enumerate(headers, start=1):
        celda = ws.cell(row=1, column=col_idx, value=label)
        celda.font = Font(bold=True)
    ws.freeze_panes = "A2"


def _ajustar_anchos(
    ws: Worksheet,
    headers: Sequence[str],
    filas_extra: Sequence[Tuple[str, str]] = (),
) -> None:
    """Auto-ajusta el ancho de columnas según el contenido más largo.

    Recorre todas las celdas materializadas en cada columna; aplica un
    mínimo y un máximo para evitar columnas microscópicas o gigantes.
    ``filas_extra`` permite considerar valores que el caller ya conoce
    (la hoja de Metadatos los pasa para no leer de la worksheet).
    """
    for col_idx, _ in enumerate(headers, start=1):
        letra = get_column_letter(col_idx)
        ancho_max = len(headers[col_idx - 1])
        for celda in ws[letra]:
            valor = celda.value
            if valor is None:
                continue
            largo = len(str(valor))
            if largo > ancho_max:
                ancho_max = largo
        for campo, valor in filas_extra:
            largo = len(campo if col_idx == 1 else valor)
            if largo > ancho_max:
                ancho_max = largo
        ancho = max(_AUTO_WIDTH_MIN, min(ancho_max + _AUTO_WIDTH_PADDING, _AUTO_WIDTH_MAX))
        ws.column_dimensions[letra].width = ancho
