"""Servicio de reportes (Sub-3.5a).

Orquesta la exportación de un reporte de asistencia a un archivo (xlsx
hoy; otros formatos en el futuro vía ``IAsistenciaExporter``) y deja
constancia inmutable en ``descargas_reportes`` + ``audit_log``.

Flujo de ``exportar_asistencia``:

    1. Validar el rango (``desde <= hasta``, fechas ISO bien formadas).
    2. Pedir al ``AsistenciaService`` la lista enriquecida del rango.
    3. Si la lista está vacía → ``ReporteSinDatosError`` (no se genera
       archivo, no se registra historial).
    4. Calcular el resumen agregado (función pura).
    5. Pedir al exporter inyectado que escriba el archivo. Si falla I/O
       → ``ReporteIOError``; tampoco se registra historial.
    6. ``descarga_repo.insert(...)`` con el ``user_id`` del actor.
    7. ``audit_logger.log(...)`` con la acción ``reporte_asistencia_exportado``.

Diseño (SOLID):
    - S: solo orquesta el caso de uso "exportar"; la decisión "qué
         dato va en cada celda" vive en el exporter.
    - D: depende de abstracciones (IAsistenciaExporter,
         IDescargaReporteRead/Write). El composition root inyecta las
         implementaciones concretas.
    - O: para soportar CSV/PDF basta con otra implementación de
         ``IAsistenciaExporter`` y elegirla en el composition root.

Idempotencia:
    Cada exportación crea SIEMPRE un archivo nuevo + una fila nueva en
    ``descargas_reportes``. No hay deduplicación por contenido. Si el
    operador exporta el mismo rango dos veces, el historial muestra dos
    filas — eso es intencional (auditoría completa de quién bajó qué y
    cuándo).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from core.models.descarga_reporte import DescargaReporte, TipoReporte
from core.repositories.descarga_reporte_repository import (
    IDescargaReporteReadRepository,
    IDescargaReporteWriteRepository,
)
from core.services.asistencia_exporter import (
    DatosReporteAsistencia,
    IAsistenciaExporter,
    calcular_resumen,
)
from core.services.asistencia_service import AsistenciaService, AsistenciaVista
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    InvalidRangoError,
    ReporteIOError,
    ReporteSinDatosError,
)
from core.services.validators import validate_fecha_iso


class ReporteService:
    """Servicio de reportes — exportación + historial."""

    def __init__(
        self,
        asistencia_service: AsistenciaService,
        descarga_read: IDescargaReporteReadRepository,
        descarga_write: IDescargaReporteWriteRepository,
        exporter: IAsistenciaExporter,
        audit_logger: AuditLogger,
    ) -> None:
        """Inicializa el servicio con sus 5 dependencias inyectadas."""
        self._asistencia = asistencia_service
        self._descarga_read = descarga_read
        self._descarga_write = descarga_write
        self._exporter = exporter
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    def contar_asistencias(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> int:
        """Cuenta filas que se exportarían con esos parámetros.

        Sirve para que la UI confirme exportes grandes ("Esto generará
        ~7800 filas, ¿continuar?") sin materializar el resultado.

        Raises:
            InvalidDateError: si alguna fecha no parsea.
            InvalidRangoError: si ``desde > hasta``.
        """
        self._validar_rango(desde, hasta)
        return self._asistencia.contar_asistencias_para_reporte(
            desde=desde, hasta=hasta, empleado_id=empleado_id
        )

    def list_historial_descargas(self, limit: int = 50) -> List[DescargaReporte]:
        """Devuelve las descargas más recientes (todas, sin filtrar usuario)."""
        return self._descarga_read.list_recientes(limit=limit)

    # ── Writes ────────────────────────────────────────────────────────────

    def exportar_asistencia(
        self,
        desde: str,
        hasta: str,
        output_path: str,
        actor_user_id: int,
        actor_username: str,
        empleado_id: Optional[int] = None,
    ) -> DescargaReporte:
        """Genera el archivo, registra el historial y deja audit log.

        Args:
            desde: ISO ``YYYY-MM-DD``, inclusivo.
            hasta: ISO ``YYYY-MM-DD``, inclusivo. Debe ser >= desde.
            output_path: Ruta absoluta donde escribir el .xlsx.
            actor_user_id: PK del usuario que disparó la acción.
            actor_username: Nombre del usuario (para hoja de Metadatos).
            empleado_id: Si se pasa, filtra al empleado indicado.

        Returns:
            Fila de ``descargas_reportes`` recién insertada con su id.

        Raises:
            InvalidDateError, InvalidRangoError: rango mal formado.
            ReporteSinDatosError: el rango no tiene asistencias.
            ReporteIOError: el archivo no se pudo escribir.
        """
        self._validar_rango(desde, hasta)
        items = self._asistencia.list_asistencias_para_reporte(
            desde=desde, hasta=hasta, empleado_id=empleado_id
        )
        if not items:
            raise ReporteSinDatosError(desde=desde, hasta=hasta, empleado_id=empleado_id)

        timestamp_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        datos = self._armar_bundle(
            items=items,
            desde=desde,
            hasta=hasta,
            empleado_id=empleado_id,
            actor_username=actor_username,
            timestamp_iso=timestamp_iso,
        )
        filas_exportadas = self._escribir_archivo(datos, output_path)
        descarga = self._descarga_write.insert(
            DescargaReporte(
                id=None,
                user_id=actor_user_id,
                fecha_hora_utc=timestamp_iso,
                tipo_reporte=TipoReporte.ASISTENCIA.value,
                rango_desde=desde,
                rango_hasta=hasta,
                empleado_id_filtro=empleado_id,
                ruta_archivo=output_path,
                filas_exportadas=filas_exportadas,
            )
        )
        self._registrar_auditoria(descarga, actor_user_id)
        self._log.info(
            "Reporte exportado: id=%s ruta=%s filas=%s actor=%s",
            descarga.id,
            output_path,
            filas_exportadas,
            actor_user_id,
        )
        return descarga

    # ── Helpers privados ──────────────────────────────────────────────────

    def _armar_bundle(
        self,
        items: List[AsistenciaVista],
        desde: str,
        hasta: str,
        empleado_id: Optional[int],
        actor_username: str,
        timestamp_iso: str,
    ) -> DatosReporteAsistencia:
        """Construye el ``DatosReporteAsistencia`` listo para el exporter."""
        resumen = calcular_resumen(items)
        nombre_filtro = self._resolver_nombre_filtro(empleado_id, items)
        return DatosReporteAsistencia(
            items=items,
            resumen_por_empleado=resumen,
            rango_desde=desde,
            rango_hasta=hasta,
            empleado_id_filtro=empleado_id,
            empleado_nombre_filtro=nombre_filtro,
            actor_username=actor_username,
            timestamp_iso=timestamp_iso,
        )

    @staticmethod
    def _resolver_nombre_filtro(
        empleado_id: Optional[int],
        items: List[AsistenciaVista],
    ) -> Optional[str]:
        """Saca el nombre del empleado filtrado de la primera fila enriquecida.

        ``items`` no está vacío en este punto (el caller ya validó). Si
        no hay filtro, devuelve ``None``.
        """
        if empleado_id is None:
            return None
        return items[0].empleado_nombre_completo

    def _escribir_archivo(
        self,
        datos: DatosReporteAsistencia,
        output_path: str,
    ) -> int:
        """Llama al exporter y traduce ``OSError`` → ``ReporteIOError``."""
        try:
            return self._exporter.exportar(datos, output_path)
        except OSError as exc:
            raise ReporteIOError(ruta=output_path, causa=str(exc)) from exc

    @staticmethod
    def _validar_rango(desde: str, hasta: str) -> None:
        """Valida formato + ``desde <= hasta``. Lanza errores específicos."""
        d_desde = validate_fecha_iso(desde, "desde")
        d_hasta = validate_fecha_iso(hasta, "hasta")
        if d_desde > d_hasta:
            raise InvalidRangoError(desde=desde, hasta=hasta)

    def _registrar_auditoria(self, descarga: DescargaReporte, actor_user_id: int) -> None:
        """Registra la exportación en ``audit_log``."""
        emp_filtro = descarga.empleado_id_filtro
        emp_filtro_json = "null" if emp_filtro is None else str(emp_filtro)
        self._audit.log(
            action="reporte_asistencia_exportado",
            user_id=actor_user_id,
            details=(
                f'{{"descarga_id": {descarga.id}, '
                f'"desde": "{descarga.rango_desde}", '
                f'"hasta": "{descarga.rango_hasta}", '
                f'"empleado_id_filtro": {emp_filtro_json}, '
                f'"filas": {descarga.filas_exportadas}}}'
            ),
        )
