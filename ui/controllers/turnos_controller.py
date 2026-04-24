"""Controller de la vista de Turnos (Sub-2.5).

Orquesta las llamadas al ``TurnoService`` para el CRUD del catálogo de
turnos. Cada método público está decorado con
``@require_permission(MANAGE_EMPLOYEES)`` — mismo permiso que usa el
botón ``shifts`` del sidebar (ver ``MainController.open_shifts``).

El controller NO conoce Tk/customtkinter. Expone métodos sincrónicos
que devuelven modelos de dominio o None; la vista decide cómo
renderizarlos y qué validación in-form aplicar antes de invocarlos.
"""

from __future__ import annotations

import logging
from typing import List

from core.models import permissions as perms
from core.models.turno import Turno
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session
from core.services.turno_service import TurnoService


class TurnosController:
    """Controller de la vista de Turnos."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        turno_service: TurnoService,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario. El decorador
                ``@require_permission`` la lee desde ``self.session``.
            permission_service: Verificador de permisos usado por el
                decorador (lee desde ``self.permission_service``).
            turno_service: Servicio con el CRUD del catálogo de turnos.
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._turno = turno_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def list_turnos(self, solo_activos: bool = True) -> List[Turno]:
        """Devuelve la lista de turnos (activos por default)."""
        return self._turno.list_turnos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def get_turno(self, turno_id: int) -> Turno:
        """Devuelve un turno por id.

        Raises:
            TurnoNotFoundError: Si no existe.
        """
        return self._turno.get_turno(turno_id)

    # ── Writes ────────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_EMPLOYEES)
    def create_turno(
        self,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
    ) -> Turno:
        """Crea un turno con validaciones del service.

        Todas las reglas de negocio (nombre único, formato HH:MM,
        bitmask válido, descanso < duración) se validan en el service.
        El controller solo pasa el ``actor_user_id`` desde la sesión.
        """
        return self._turno.create_turno(
            nombre=nombre,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            minutos_descanso=minutos_descanso,
            dias_semana=dias_semana,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def update_turno(
        self,
        turno_id: int,
        nombre: str,
        hora_entrada: str,
        hora_salida: str,
        minutos_descanso: int,
        dias_semana: int,
    ) -> None:
        """Actualiza todos los campos editables de un turno.

        No toca ``is_active`` — para eso están
        ``archive_turno`` / ``unarchive_turno``.
        """
        self._turno.update_turno(
            turno_id=turno_id,
            nombre=nombre,
            hora_entrada=hora_entrada,
            hora_salida=hora_salida,
            minutos_descanso=minutos_descanso,
            dias_semana=dias_semana,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_EMPLOYEES)
    def archive_turno(self, turno_id: int) -> None:
        """Archiva un turno (soft-delete)."""
        self._turno.archive_turno(turno_id, self.session.user_id)

    @require_permission(perms.MANAGE_EMPLOYEES)
    def unarchive_turno(self, turno_id: int) -> None:
        """Reactiva un turno archivado."""
        self._turno.unarchive_turno(turno_id, self.session.user_id)
