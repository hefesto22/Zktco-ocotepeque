"""Controller de la vista de configuración maestra (Sub-2.4).

Orquesta las llamadas al ``CatalogoService`` para departamentos y cargos.
Cada método público está decorado con ``@require_permission(MANAGE_SETTINGS)``
— si un botón aparece en la UI por un bug de ``filter_visible``, el click
terminará en ``PermissionDeniedError`` antes de tocar el servicio.

El controller NO conoce Tk/customtkinter. Expone métodos sincrónicos que
devuelven modelos de dominio o None; la vista decide cómo renderizarlos.
"""

from __future__ import annotations

import logging
from typing import List

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.services.catalogo_service import CatalogoService
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session


class ConfiguracionController:
    """Controller de la vista de configuración (departamentos + cargos)."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        catalogo_service: CatalogoService,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario. El decorador
                ``@require_permission`` la lee desde ``self.session``.
            permission_service: Verificador de permisos usado por el
                decorador (lee desde ``self.permission_service``).
            catalogo_service: Servicio con el CRUD de ambos catálogos.
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._catalogo = catalogo_service
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Departamentos ─────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        """Devuelve la lista de departamentos (activos por default)."""
        return self._catalogo.list_departamentos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_SETTINGS)
    def create_departamento(self, nombre: str) -> Departamento:
        """Crea un departamento nuevo y lo devuelve con su id asignado."""
        return self._catalogo.create_departamento(nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def rename_departamento(self, departamento_id: int, nuevo_nombre: str) -> None:
        """Renombra un departamento existente."""
        self._catalogo.rename_departamento(departamento_id, nuevo_nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_departamento(self, departamento_id: int) -> None:
        """Archiva un departamento (falla si tiene empleados activos)."""
        self._catalogo.archive_departamento(departamento_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_departamento(self, departamento_id: int) -> None:
        """Reactiva un departamento archivado."""
        self._catalogo.unarchive_departamento(departamento_id, self.session.user_id)

    # ── Cargos ────────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        """Devuelve la lista de cargos (activos por default)."""
        return self._catalogo.list_cargos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_SETTINGS)
    def create_cargo(self, nombre: str) -> Cargo:
        """Crea un cargo nuevo y lo devuelve con su id asignado."""
        return self._catalogo.create_cargo(nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def rename_cargo(self, cargo_id: int, nuevo_nombre: str) -> None:
        """Renombra un cargo existente."""
        self._catalogo.rename_cargo(cargo_id, nuevo_nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_cargo(self, cargo_id: int) -> None:
        """Archiva un cargo (falla si tiene empleados activos)."""
        self._catalogo.archive_cargo(cargo_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_cargo(self, cargo_id: int) -> None:
        """Reactiva un cargo archivado."""
        self._catalogo.unarchive_cargo(cargo_id, self.session.user_id)
