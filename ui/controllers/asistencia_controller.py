"""Controller de la vista de Asistencia (Sub-3.4b).

Expone tres métodos de uso directo desde la vista, cada uno protegido
por ``@require_permission(VIEW_ATTENDANCE)``:

    - list_asistencias(desde, hasta, empleado_id=None)
      Lista el rango con DTOs enriquecidos (nombre, DNI, turno) y la
      bandera ``truncado`` para avisos de UI.
    - list_empleados_para_filtro()
      Combo del filtro de la cabecera.
    - update_observaciones(asistencia_id, texto)
      Guarda anotación manual; el controller inyecta ``actor_user_id``
      desde la ``Session`` activa para que la vista no tenga que
      conocer el id del usuario logueado.

Diseño:
    - El controller NO conoce Tk/customtkinter — devuelve modelos /
      DTOs y propaga excepciones de dominio para que la vista decida
      cómo presentarlas.
    - Para que ``@require_permission`` funcione, se exponen ``self.session``
      y ``self.permission_service`` como atributos públicos (mismo
      contrato que ``EmpleadosController``, ``SincronizacionController``,
      etc.).
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from core.models import permissions as perms
from core.services.asistencia_service import (
    AsistenciaService,
    ResultadoBusquedaAsistencia,
)
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session


class AsistenciaController:
    """Controller de la vista de Asistencia (consulta + edición de observaciones)."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        asistencia_service: AsistenciaService,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa. Leída por el decorador vía
                ``self.session``. También se usa para resolver
                ``actor_user_id`` al actualizar observaciones.
            permission_service: Verificador de permisos (leído por el
                decorador vía ``self.permission_service``).
            asistencia_service: Servicio con la lógica de listado y
                edición.
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._asistencia = asistencia_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    @require_permission(perms.VIEW_ATTENDANCE)
    def list_asistencias(
        self,
        desde: str,
        hasta: str,
        empleado_id: Optional[int] = None,
    ) -> ResultadoBusquedaAsistencia:
        """Devuelve las asistencias del rango, enriquecidas y con bandera de trunc."""
        return self._asistencia.list_asistencias(desde=desde, hasta=hasta, empleado_id=empleado_id)

    @require_permission(perms.VIEW_ATTENDANCE)
    def list_empleados_para_filtro(self) -> List[Tuple[int, str]]:
        """Devuelve ``[(empleado_id, "apellidos nombres")]`` para el combo."""
        return self._asistencia.list_empleados_para_filtro()

    # ── Writes ────────────────────────────────────────────────────────────

    @require_permission(perms.VIEW_ATTENDANCE)
    def update_observaciones(
        self,
        asistencia_id: int,
        observaciones: Optional[str],
    ) -> None:
        """Actualiza la observación manual de una fila de asistencia.

        El ``actor_user_id`` lo provee el controller desde la sesión
        activa — la vista no necesita conocerlo.

        Raises:
            AsistenciaNotFoundError: Si el id no existe.
        """
        self._asistencia.update_observaciones(
            asistencia_id=asistencia_id,
            observaciones=observaciones,
            actor_user_id=self.session.user_id,
        )
