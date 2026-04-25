"""Tests del XlsxAsistenciaExporter (infraestructura openpyxl).

Cubre la generación del archivo .xlsx multi-hoja:
    - Las 3 hojas existen con los nombres esperados.
    - Headers en la primera fila + freeze panes en A2.
    - Datos del Resumen y Detalle se vuelcan correctamente.
    - Hoja Metadatos contiene rango, filtro, actor, timestamp.
    - ``exportar`` devuelve la cantidad de filas de detalle.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from openpyxl import load_workbook

from core.models.asistencia import Asistencia, EstadoAsistencia
from core.services.asistencia_exporter import (
    DatosReporteAsistencia,
    ResumenEmpleado,
)
from core.services.asistencia_service import AsistenciaVista
from infrastructure.exporters.xlsx_asistencia_exporter import XlsxAsistenciaExporter


def _vista(
    emp_id: int,
    nombre: str,
    fecha: str,
    estado: str = EstadoAsistencia.PRESENTE.value,
) -> AsistenciaVista:
    return AsistenciaVista(
        asistencia=Asistencia(
            id=emp_id * 1000,
            empleado_id=emp_id,
            fecha=fecha,
            estado=estado,
            turno_id_aplicado=1,
            hora_entrada_real="08:00:00",
            hora_salida_real="17:00:00",
            observaciones=None,
        ),
        empleado_nombre_completo=nombre,
        empleado_dni="0801-1990-12345",
        turno_nombre="Admin 8-5",
    )


def _resumen(emp_id: int, nombre: str) -> ResumenEmpleado:
    return ResumenEmpleado(
        empleado_id=emp_id,
        empleado_nombre=nombre,
        empleado_dni="0801-1990-12345",
        dias_presente=10,
        dias_tarde=2,
        dias_salida_temprana=1,
        dias_ausente=0,
        dias_feriado=0,
        dias_sin_turno=4,
        dias_incompleto=0,
        total_minutos_tarde=25,
        total_minutos_salida_temprana=10,
    )


def _datos(
    items: Optional[List[AsistenciaVista]] = None,
    resumen: Optional[List[ResumenEmpleado]] = None,
    empleado_id_filtro: Optional[int] = None,
    empleado_nombre_filtro: Optional[str] = None,
) -> DatosReporteAsistencia:
    if items is None:
        items = [_vista(1, "Pérez Juan", "2026-04-15")]
    if resumen is None:
        resumen = [_resumen(1, "Pérez Juan")]
    return DatosReporteAsistencia(
        items=items,
        resumen_por_empleado=resumen,
        rango_desde="2026-04-01",
        rango_hasta="2026-04-30",
        empleado_id_filtro=empleado_id_filtro,
        empleado_nombre_filtro=empleado_nombre_filtro,
        actor_username="alice",
        timestamp_iso="2026-04-24T10:00:00+00:00",
    )


# ── Path feliz ────────────────────────────────────────────────────────────────


def test_exportar_devuelve_cantidad_de_filas_de_detalle(tmp_path: Path) -> None:
    items = [
        _vista(1, "Pérez Juan", "2026-04-15"),
        _vista(1, "Pérez Juan", "2026-04-16"),
        _vista(2, "López Bob", "2026-04-15"),
    ]
    out = tmp_path / "rep.xlsx"
    cantidad = XlsxAsistenciaExporter().exportar(_datos(items=items), str(out))
    assert cantidad == 3
    assert out.exists()


def test_exportar_crea_tres_hojas(tmp_path: Path) -> None:
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(_datos(), str(out))
    wb = load_workbook(str(out))
    assert wb.sheetnames == ["Resumen", "Detalle", "Metadatos"]


# ── Hoja Resumen ──────────────────────────────────────────────────────────────


def test_resumen_tiene_headers_y_freeze(tmp_path: Path) -> None:
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(_datos(), str(out))
    wb = load_workbook(str(out))
    ws = wb["Resumen"]
    assert ws.cell(row=1, column=1).value == "Empleado"
    assert ws.cell(row=1, column=1).font.bold is True
    assert ws.freeze_panes == "A2"


def test_resumen_pinta_una_fila_por_empleado(tmp_path: Path) -> None:
    resumen = [_resumen(1, "Pérez Juan"), _resumen(2, "López Bob")]
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(_datos(resumen=resumen), str(out))
    ws = load_workbook(str(out))["Resumen"]
    nombres = [ws.cell(row=r, column=1).value for r in (2, 3)]
    assert nombres == ["Pérez Juan", "López Bob"]
    # Días presente está en la columna 3 según el layout.
    assert ws.cell(row=2, column=3).value == 10


# ── Hoja Detalle ──────────────────────────────────────────────────────────────


def test_detalle_pinta_una_fila_por_item(tmp_path: Path) -> None:
    items = [
        _vista(1, "Pérez Juan", "2026-04-15"),
        _vista(1, "Pérez Juan", "2026-04-16"),
    ]
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(_datos(items=items), str(out))
    ws = load_workbook(str(out))["Detalle"]
    fechas = [ws.cell(row=r, column=1).value for r in (2, 3)]
    assert fechas == ["2026-04-15", "2026-04-16"]
    assert ws.cell(row=2, column=2).value == "Pérez Juan"
    assert ws.cell(row=2, column=5).value == EstadoAsistencia.PRESENTE.value


def test_detalle_observaciones_none_se_renderiza_como_string_vacio(
    tmp_path: Path,
) -> None:
    """El xlsx no tolera ``None`` para celdas vacías visuales — usamos ``""``."""
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(_datos(), str(out))
    ws = load_workbook(str(out))["Detalle"]
    # Columna 10 = Observaciones (según layout).
    assert ws.cell(row=2, column=10).value in ("", None)


# ── Hoja Metadatos ────────────────────────────────────────────────────────────


def test_metadatos_sin_filtro_dice_todos(tmp_path: Path) -> None:
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(_datos(), str(out))
    ws = load_workbook(str(out))["Metadatos"]
    pares = {
        ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
        for r in range(2, ws.max_row + 1)
    }
    assert pares["Filtro de empleado"] == "(todos los empleados)"
    assert pares["Generado por"] == "alice"
    assert pares["Rango desde"] == "2026-04-01"
    assert pares["Rango hasta"] == "2026-04-30"


def test_metadatos_con_filtro_muestra_nombre(tmp_path: Path) -> None:
    out = tmp_path / "rep.xlsx"
    XlsxAsistenciaExporter().exportar(
        _datos(empleado_id_filtro=7, empleado_nombre_filtro="Pérez Juan"),
        str(out),
    )
    ws = load_workbook(str(out))["Metadatos"]
    pares = {
        ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
        for r in range(2, ws.max_row + 1)
    }
    assert pares["Filtro de empleado"] == "Pérez Juan"
