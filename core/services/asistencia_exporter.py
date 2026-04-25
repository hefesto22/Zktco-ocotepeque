"""Interfaz e infra de soporte para exportar reportes de asistencia.

Este módulo es la "frontera" entre el dominio (qué datos vamos a
exportar) y la infraestructura (cómo se materializa el archivo).
Vive bajo ``core/services/`` porque las dataclasses y la interfaz
forman parte del contrato del servicio — la implementación concreta
(openpyxl) vive en ``infrastructure/exporters/``.

Diseño (SOLID):
    - O (Open/Closed): nuevos formatos (CSV, PDF) implementan
      ``IAsistenciaExporter`` sin tocar ``ReporteService``.
    - S: cada exportador solo sabe escribir su formato; el cálculo
      del resumen es una función pura compartida (``calcular_resumen``).
    - D: ``ReporteService`` depende de la abstracción
      (``IAsistenciaExporter``), no de openpyxl.

Reglas de cálculo del resumen:
    Un día cuenta para una sola categoría EXCEPTO ``TARDE_Y_SALIDA_TEMPRANA``
    que cuenta para AMBAS — ``dias_tarde`` y ``dias_salida_temprana``.
    Esta es la convención del negocio: "el empleado tuvo 4 días tarde,
    incluyendo 1 día que también salió temprano".

    ``minutos_tarde`` y ``minutos_salida_temprana`` se acumulan tal cual
    los reporta la consolidación — no se duplican ni se cruzan.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.models.asistencia import EstadoAsistencia
from core.services.asistencia_service import AsistenciaVista


@dataclass(frozen=True)
class ResumenEmpleado:
    """Conteo agregado de días por estado para un empleado en el rango.

    Un día con ``TARDE_Y_SALIDA_TEMPRANA`` se cuenta en AMBAS columnas
    (``dias_tarde`` + ``dias_salida_temprana``). El resto de estados
    son mutuamente excluyentes.

    Atributos:
        empleado_id: PK del empleado.
        empleado_nombre: ``"apellidos nombres"`` ya formateado.
        empleado_dni: DNI o ``None`` si el empleado fue borrado.
        dias_presente: Conteo de ``PRESENTE``.
        dias_tarde: Conteo de ``TARDE`` + ``TARDE_Y_SALIDA_TEMPRANA``.
        dias_salida_temprana: Conteo de ``SALIDA_TEMPRANA`` +
            ``TARDE_Y_SALIDA_TEMPRANA``.
        dias_ausente: Conteo de ``AUSENTE``.
        dias_feriado: Conteo de ``FERIADO``.
        dias_sin_turno: Conteo de ``SIN_TURNO``.
        dias_incompleto: Conteo de ``INCOMPLETO``.
        total_minutos_tarde: Suma de ``minutos_tarde`` del rango.
        total_minutos_salida_temprana: Suma de
            ``minutos_salida_temprana`` del rango.
    """

    empleado_id: int
    empleado_nombre: str
    empleado_dni: Optional[str]
    dias_presente: int
    dias_tarde: int
    dias_salida_temprana: int
    dias_ausente: int
    dias_feriado: int
    dias_sin_turno: int
    dias_incompleto: int
    total_minutos_tarde: int
    total_minutos_salida_temprana: int


@dataclass(frozen=True)
class DatosReporteAsistencia:
    """Bundle de datos listos para exportar a un archivo.

    Lo arma ``ReporteService`` y lo consume ``IAsistenciaExporter``.
    Es ``frozen`` porque el exporter no debe mutar el contenido.

    Atributos:
        items: Filas crudas enriquecidas (una por empleado-fecha).
        resumen_por_empleado: Resumen agregado, ordenado por nombre.
        rango_desde: ISO ``YYYY-MM-DD``, inclusivo.
        rango_hasta: ISO ``YYYY-MM-DD``, inclusivo.
        empleado_id_filtro: Si se filtró por un empleado específico,
            su id; ``None`` si el reporte abarca a todos.
        empleado_nombre_filtro: Nombre del empleado filtrado para
            mostrar en la hoja de Metadatos. ``None`` cuando no hay
            filtro.
        actor_username: Usuario que generó el reporte (para Metadatos).
        timestamp_iso: Marca temporal del reporte (UTC ISO-8601).
    """

    items: List[AsistenciaVista]
    resumen_por_empleado: List[ResumenEmpleado]
    rango_desde: str
    rango_hasta: str
    empleado_id_filtro: Optional[int]
    empleado_nombre_filtro: Optional[str]
    actor_username: str
    timestamp_iso: str


class IAsistenciaExporter(ABC):
    """Interfaz de exportador de reportes de asistencia.

    Cada implementación (xlsx, csv, pdf) decide su propio layout. El
    contrato común es: "recibe datos + ruta destino, escribe el archivo
    y devuelve cuántas filas de detalle escribió".
    """

    @abstractmethod
    def exportar(self, datos: DatosReporteAsistencia, output_path: str) -> int:
        """Escribe el archivo con los datos recibidos.

        Args:
            datos: Bundle ya armado por ``ReporteService``.
            output_path: Ruta absoluta del archivo a generar. La
                implementación lo crea (sobrescribe si existe).

        Returns:
            Cantidad de filas de detalle escritas (== ``len(items)``).
            Útil para registrar en ``descargas_reportes.filas_exportadas``.

        Raises:
            OSError: cualquier fallo de I/O — sin permisos, disco lleno,
                ruta inválida, archivo bloqueado por otro proceso.
                ``ReporteService`` lo traduce a ``ReporteIOError``.
        """


# ── Función pura compartida ──────────────────────────────────────────────────


def calcular_resumen(items: List[AsistenciaVista]) -> List[ResumenEmpleado]:
    """Agrupa por empleado y calcula contadores de días + minutos.

    Función pura: no toca BD ni I/O. Se invoca desde
    ``ReporteService.exportar_asistencia`` y se testea de forma aislada.

    Args:
        items: Filas enriquecidas. Pueden venir desordenadas; el
            resultado se ordena por nombre ascendente.

    Returns:
        Lista de ``ResumenEmpleado`` ordenada por nombre. Si ``items``
        está vacío, devuelve ``[]``.
    """
    acumulador: Dict[int, _AcumEmpleado] = {}
    for vista in items:
        emp_id = vista.asistencia.empleado_id
        slot = acumulador.get(emp_id)
        if slot is None:
            slot = _AcumEmpleado(
                empleado_nombre=vista.empleado_nombre_completo,
                empleado_dni=vista.empleado_dni,
            )
            acumulador[emp_id] = slot
        _sumar_dia(slot, vista)
    resumenes = [_slot_a_resumen(emp_id, slot) for emp_id, slot in acumulador.items()]
    resumenes.sort(key=lambda r: r.empleado_nombre.lower())
    return resumenes


# ── Helpers privados de cálculo ──────────────────────────────────────────────


@dataclass
class _AcumEmpleado:
    """Bucket mutable para acumular conteos durante el agrupamiento."""

    empleado_nombre: str
    empleado_dni: Optional[str]
    dias_presente: int = 0
    dias_tarde: int = 0
    dias_salida_temprana: int = 0
    dias_ausente: int = 0
    dias_feriado: int = 0
    dias_sin_turno: int = 0
    dias_incompleto: int = 0
    total_minutos_tarde: int = 0
    total_minutos_salida_temprana: int = 0


# Mapa estado → atributos del bucket a incrementar. ``TARDE_Y_SALIDA_TEMPRANA``
# es el único caso múltiple: cuenta como tarde y como salida temprana.
_INCREMENTOS_POR_ESTADO: Dict[str, Tuple[str, ...]] = {
    EstadoAsistencia.PRESENTE.value: ("dias_presente",),
    EstadoAsistencia.TARDE.value: ("dias_tarde",),
    EstadoAsistencia.SALIDA_TEMPRANA.value: ("dias_salida_temprana",),
    EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value: (
        "dias_tarde",
        "dias_salida_temprana",
    ),
    EstadoAsistencia.AUSENTE.value: ("dias_ausente",),
    EstadoAsistencia.FERIADO.value: ("dias_feriado",),
    EstadoAsistencia.SIN_TURNO.value: ("dias_sin_turno",),
    EstadoAsistencia.INCOMPLETO.value: ("dias_incompleto",),
}


def _sumar_dia(slot: _AcumEmpleado, vista: AsistenciaVista) -> None:
    """Incrementa los contadores del bucket según el estado del día."""
    asist = vista.asistencia
    for atributo in _INCREMENTOS_POR_ESTADO.get(asist.estado, ()):
        setattr(slot, atributo, getattr(slot, atributo) + 1)
    slot.total_minutos_tarde += asist.minutos_tarde
    slot.total_minutos_salida_temprana += asist.minutos_salida_temprana


def _slot_a_resumen(empleado_id: int, slot: _AcumEmpleado) -> ResumenEmpleado:
    """Convierte el bucket mutable al dataclass público inmutable."""
    return ResumenEmpleado(
        empleado_id=empleado_id,
        empleado_nombre=slot.empleado_nombre,
        empleado_dni=slot.empleado_dni,
        dias_presente=slot.dias_presente,
        dias_tarde=slot.dias_tarde,
        dias_salida_temprana=slot.dias_salida_temprana,
        dias_ausente=slot.dias_ausente,
        dias_feriado=slot.dias_feriado,
        dias_sin_turno=slot.dias_sin_turno,
        dias_incompleto=slot.dias_incompleto,
        total_minutos_tarde=slot.total_minutos_tarde,
        total_minutos_salida_temprana=slot.total_minutos_salida_temprana,
    )
