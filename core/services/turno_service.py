"""Servicio de turnos de trabajo.

Orquesta el CRUD de la tabla ``turnos``. No toca ``empleado_turnos`` —
las asignaciones a empleados son responsabilidad de ``EmpleadoService``
(ver Decisión arquitectural de Sub-2.3: Opción A aprobada).

Reglas de negocio:

    - Nombre único a nivel catálogo. Check explícito antes del
      INSERT/UPDATE → ``DuplicateTurnoNombreError``.
    - Horas validadas como ``HH:MM`` 24h (rango 00:00..23:59).
    - ``cruza_medianoche`` es DERIVADO — se calcula desde las horas,
      no se recibe del caller. La UI no tiene que saber esta regla.
    - Bitmask de días en 1..127 (el 0 se rechaza — un turno sin días
      aplicables no tendría uso).
    - ``minutos_descanso >= 0`` y estrictamente menor que la duración
      total del bloque (evita turnos de 4h con 5h de "descanso").
    - Archivar NO valida asignaciones existentes (decisión de diseño:
      los vigentes siguen activos aunque el turno esté archivado; la
      restricción real es al ASIGNAR, impuesta por ``EmpleadoService``).

Diseño (SOLID):
    - S: solo gestiona el catálogo de turnos. No asigna, no verifica
         permisos, no lee empleados.
    - D: recibe ``ITurnoReadRepository``, ``ITurnoWriteRepository`` y
         ``AuditLogger`` por inyección.
"""

from __future__ import annotations

import logging
from typing import List

from core.models.turno import Turno
from core.repositories.turno_repository import (
    ITurnoReadRepository,
    ITurnoWriteRepository,
)
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    DuplicateTurnoNombreError,
    InvalidDescansoError,
    TurnoNotFoundError,
)
from core.services.validators import (
    cruza_medianoche,
    duracion_turno_minutos,
    normalize_nombre,
    validate_bitmask_dias,
    validate_hora_hhmm,
    validate_minutos_descanso,
)


class TurnoService:
    """CRUD del catálogo de turnos con validaciones de dominio."""

    def __init__(
        self,
        turno_read: ITurnoReadRepository,
        turno_write: ITurnoWriteRepository,
        audit_logger: AuditLogger,
    ) -> None:
        """Inicializa el servicio con sus dependencias inyectadas."""
        self._turno_read = turno_read
        self._turno_write = turno_write
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_turnos(self, solo_activos: bool = True) -> List[Turno]:
        """Devuelve la lista de turnos (activos por default)."""
        if solo_activos:
            return self._turno_read.list_active()
        return self._turno_read.list_all()

    def get_turno(self, turno_id: int) -> Turno:
        """Devuelve un turno por id.

        Raises:
            TurnoNotFoundError: Si no existe el id.
        """
        turno = self._turno_read.get_by_id(turno_id)
        if turno is None:
            raise TurnoNotFoundError(turno_id)
        return turno

    # ── Writes ────────────────────────────────────────────────────────────

    def create_turno(
        self,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
        actor_user_id: int,
    ) -> Turno:
        """Crea un turno nuevo con validaciones completas.

        Args:
            nombre: Texto libre. Se normaliza (trim + colapso espacios).
            hora_entrada: ``"HH:MM"`` 24h.
            hora_salida: ``"HH:MM"`` 24h. Si menor o igual a
                ``hora_entrada``, el turno cruza medianoche.
            minutos_descanso: >= 0 y < duración total del turno.
            dias_semana: Bitmask 1..127.
            actor_user_id: Usuario que ejecuta (audit_log).

        Returns:
            El turno creado con ``id`` asignado.

        Raises:
            MissingRequiredFieldError: Nombre u horas vacías.
            InvalidTimeError: Formato de hora inválido.
            InvalidBitmaskError: ``dias_semana`` fuera de 1..127.
            InvalidDescansoError: ``minutos_descanso`` >= duración.
            DuplicateTurnoNombreError: Ya existe un turno con ese nombre.
        """
        nombre_norm = normalize_nombre(nombre, "nombre")
        self._validar_bloque(hora_entrada, hora_salida, minutos_descanso, dias_semana)

        if self._turno_read.get_by_nombre(nombre_norm) is not None:
            raise DuplicateTurnoNombreError(nombre_norm)

        cruza = cruza_medianoche(hora_entrada, hora_salida)
        creado = self._turno_write.create(
            Turno(
                id=None,
                nombre=nombre_norm,
                hora_entrada=hora_entrada,
                hora_salida=hora_salida,
                minutos_descanso=minutos_descanso,
                dias_semana=dias_semana,
                cruza_medianoche=cruza,
                is_active=True,
            )
        )
        assert creado.id is not None
        self._audit.log(
            action="turno_created",
            user_id=actor_user_id,
            details=f'{{"id": {creado.id}, "nombre": "{nombre_norm}"}}',
        )
        self._log.info(
            "Turno creado: id=%s nombre=%s cruza=%s",
            creado.id,
            nombre_norm,
            cruza,
        )
        return creado

    def update_turno(
        self,
        turno_id: int,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
        actor_user_id: int,
    ) -> None:
        """Actualiza todos los campos editables de un turno.

        No altera ``is_active`` — para eso están ``archive_turno`` y
        ``unarchive_turno``.

        Raises:
            TurnoNotFoundError: ``turno_id`` no existe.
            DuplicateTurnoNombreError: El nuevo nombre colisiona con otro.
            (Y todas las ``ValidationError`` de las horas/bitmask/descanso.)
        """
        existente = self.get_turno(turno_id)
        nombre_norm = normalize_nombre(nombre, "nombre")
        self._validar_bloque(hora_entrada, hora_salida, minutos_descanso, dias_semana)

        if nombre_norm != existente.nombre:
            colision = self._turno_read.get_by_nombre(nombre_norm)
            if colision is not None and colision.id != turno_id:
                raise DuplicateTurnoNombreError(nombre_norm)

        cruza = cruza_medianoche(hora_entrada, hora_salida)
        self._turno_write.update(
            Turno(
                id=turno_id,
                nombre=nombre_norm,
                hora_entrada=hora_entrada,
                hora_salida=hora_salida,
                minutos_descanso=minutos_descanso,
                dias_semana=dias_semana,
                cruza_medianoche=cruza,
                is_active=existente.is_active,
            )
        )
        self._audit.log(
            action="turno_updated",
            user_id=actor_user_id,
            details=f'{{"id": {turno_id}, "nombre": "{nombre_norm}"}}',
        )
        self._log.info("Turno actualizado: id=%s nombre=%s", turno_id, nombre_norm)

    def archive_turno(self, turno_id: int, actor_user_id: int) -> None:
        """Archiva un turno.

        No valida asignaciones vigentes — por decisión de diseño, los
        empleados con este turno siguen vigentes aunque el turno esté
        archivado. La restricción real es al asignar.

        Raises:
            TurnoNotFoundError: ``turno_id`` no existe.
        """
        turno = self.get_turno(turno_id)
        if not turno.is_active:
            return
        self._turno_write.archive(turno_id)
        self._audit.log(
            action="turno_archived",
            user_id=actor_user_id,
            details=f'{{"id": {turno_id}}}',
        )
        self._log.info("Turno archivado: id=%s", turno_id)

    def unarchive_turno(self, turno_id: int, actor_user_id: int) -> None:
        """Reactiva un turno archivado.

        Raises:
            TurnoNotFoundError: ``turno_id`` no existe.
        """
        turno = self.get_turno(turno_id)
        if turno.is_active:
            return
        self._turno_write.unarchive(turno_id)
        self._audit.log(
            action="turno_unarchived",
            user_id=actor_user_id,
            details=f'{{"id": {turno_id}}}',
        )
        self._log.info("Turno reactivado: id=%s", turno_id)

    # ── Helpers privados ──────────────────────────────────────────────────

    @staticmethod
    def _validar_bloque(
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
    ) -> None:
        """Ejecuta todas las validaciones del bloque del turno.

        Orden intencionado: primero las validaciones de formato (barato y
        dan errores claros de input), luego la regla semántica del
        descanso (requiere cálculos).
        """
        validate_hora_hhmm(hora_entrada, "hora_entrada")
        validate_hora_hhmm(hora_salida, "hora_salida")
        validate_bitmask_dias(dias_semana)
        validate_minutos_descanso(minutos_descanso)

        duracion = duracion_turno_minutos(hora_entrada, hora_salida)
        if minutos_descanso >= duracion:
            raise InvalidDescansoError(minutos_descanso, duracion)
