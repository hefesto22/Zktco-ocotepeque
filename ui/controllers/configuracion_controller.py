"""Controller de la vista de configuración maestra (Sub-2.4 + Sub-2.4b).

Orquesta las llamadas al ``CatalogoService`` (departamentos + cargos) y
al ``DispositivoConfigService`` (Plan B / Sub-2.4b: catálogo de
dispositivos ZKTeco). Cada método público está decorado con
``@require_permission(MANAGE_SETTINGS)`` — si un botón aparece en la UI
por un bug de ``filter_visible``, el click terminará en
``PermissionDeniedError`` antes de tocar el servicio.

El controller NO conoce Tk/customtkinter. Expone métodos sincrónicos que
devuelven modelos de dominio o None; la vista decide cómo renderizarlos.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.models.dispositivo import Dispositivo
from core.services.catalogo_service import CatalogoService
from core.services.dispositivo_config_service import DispositivoConfigService
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session


class ConfiguracionController:
    """Controller de Configuración (departamentos + cargos + dispositivos)."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        catalogo_service: CatalogoService,
        dispositivo_service: DispositivoConfigService,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del usuario. El decorador
                ``@require_permission`` la lee desde ``self.session``.
            permission_service: Verificador de permisos usado por el
                decorador (lee desde ``self.permission_service``).
            catalogo_service: Servicio con el CRUD de departamentos y
                cargos.
            dispositivo_service: Servicio con el CRUD del catálogo de
                dispositivos ZKTeco (Sub-2.4b).
        """
        # Nombres obligatorios para que ``@require_permission`` funcione.
        self.session = session
        self.permission_service = permission_service
        self._catalogo = catalogo_service
        self._dispositivo = dispositivo_service
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
    def create_cargo(
        self,
        nombre: str,
        departamento_id: Optional[int] = None,
    ) -> Cargo:
        """Crea un cargo nuevo y lo devuelve con su id asignado.

        Sub-3.2.A: ``departamento_id`` opcional restringe el cargo a un
        departamento concreto. Sin él (o con ``None``), el cargo es global.
        """
        return self._catalogo.create_cargo(
            nombre, self.session.user_id, departamento_id=departamento_id
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def rename_cargo(self, cargo_id: int, nuevo_nombre: str) -> None:
        """Renombra un cargo existente."""
        self._catalogo.rename_cargo(cargo_id, nuevo_nombre, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def set_cargo_departamento(self, cargo_id: int, departamento_id: Optional[int]) -> None:
        """Sub-3.2.A: cambia (o desasigna con ``None``) el departamento del cargo."""
        self._catalogo.set_cargo_departamento(cargo_id, departamento_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def list_cargos_para_departamento(self, departamento_id: int) -> List[Cargo]:
        """Sub-3.2.A: cargos disponibles para un depto (globales + específicos)."""
        return self._catalogo.list_cargos_para_departamento(departamento_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_cargo(self, cargo_id: int) -> None:
        """Archiva un cargo (falla si tiene empleados activos)."""
        self._catalogo.archive_cargo(cargo_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_cargo(self, cargo_id: int) -> None:
        """Reactiva un cargo archivado."""
        self._catalogo.unarchive_cargo(cargo_id, self.session.user_id)

    # ── Dispositivos ZKTeco (Sub-2.4b / Plan B) ───────────────────────────

    @require_permission(perms.MANAGE_SETTINGS)
    def list_dispositivos(self, solo_activos: bool = True) -> List[Dispositivo]:
        """Devuelve la lista de dispositivos (activos por default)."""
        return self._dispositivo.list_dispositivos(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_SETTINGS)
    def create_dispositivo(self, nombre: str, ip: str, puerto: int) -> Dispositivo:
        """Crea un dispositivo nuevo y lo devuelve con su id asignado."""
        return self._dispositivo.create_dispositivo(
            nombre=nombre,
            ip=ip,
            puerto=puerto,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def update_dispositivo(
        self,
        dispositivo_id: int,
        nombre: str,
        ip: str,
        puerto: int,
    ) -> None:
        """Actualiza nombre, IP y puerto de un dispositivo existente."""
        self._dispositivo.update_dispositivo(
            dispositivo_id=dispositivo_id,
            nombre=nombre,
            ip=ip,
            puerto=puerto,
            actor_user_id=self.session.user_id,
        )

    @require_permission(perms.MANAGE_SETTINGS)
    def archive_dispositivo(self, dispositivo_id: int) -> None:
        """Archiva un dispositivo (soft-delete; preserva historial)."""
        self._dispositivo.archive_dispositivo(dispositivo_id, self.session.user_id)

    @require_permission(perms.MANAGE_SETTINGS)
    def unarchive_dispositivo(self, dispositivo_id: int) -> None:
        """Reactiva un dispositivo archivado."""
        self._dispositivo.unarchive_dispositivo(dispositivo_id, self.session.user_id)
