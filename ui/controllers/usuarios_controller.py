"""Controller del módulo "Usuarios y roles" (Sub-2.7a).

Wrapper delgado sobre ``UsuarioAdminService`` con guards de permisos.
Cada método público:
    - está decorado con ``@require_permission(MANAGE_USERS)``,
    - delega al service pasando ``session.user_id`` como actor +
      ``session.role_code`` para que el service aplique R1/R4.

El controller NO conoce Tk/customtkinter ni SQLite; recibe el service
inyectado por el composition root.
"""

from __future__ import annotations

import logging
from typing import List

from core.models import permissions as perms
from core.models.rol import Rol
from core.services.permission_service import PermissionService, require_permission
from core.services.session import Session
from core.services.usuario_admin_service import UsuarioAdminService, UsuarioConRol
from core.repositories.rol_repository import IRolReadRepository


class UsuariosController:
    """Controller del módulo Usuarios y roles."""

    def __init__(
        self,
        session: Session,
        permission_service: PermissionService,
        usuario_admin_service: UsuarioAdminService,
        rol_read: IRolReadRepository,
    ) -> None:
        """Inicializa el controller.

        Args:
            session: Sesión activa del actor (lo lee el decorador).
            permission_service: Verificador de permisos.
            usuario_admin_service: Service que realiza el trabajo real.
            rol_read: Para poblar el combo de roles en la UI. Se expone
                en el controller (no en el service) porque es lectura
                pura sin reglas de negocio — el filtrado de SUPERADMIN
                cuando el actor es ADMIN se hace acá.
        """
        self.session = session
        self.permission_service = permission_service
        self._service = usuario_admin_service
        self._rol_read = rol_read
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Lectura ───────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_USERS)
    def list_usuarios(self, solo_activos: bool = False) -> List[UsuarioConRol]:
        """Devuelve la lista de usuarios con su rol resuelto."""
        return self._service.list_usuarios(solo_activos=solo_activos)

    @require_permission(perms.MANAGE_USERS)
    def list_roles_asignables(self) -> List[Rol]:
        """Devuelve los roles que el actor puede asignar.

        Si el actor es SUPERADMIN, devuelve todos los roles.
        Si NO es SUPERADMIN (típicamente ADMIN), excluye SUPERADMIN del
        combo — defensa primaria de R1 (la secundaria está en el service).
        """
        todos = self._rol_read.list_all()
        if self.session.role_code == perms.ROLE_SUPERADMIN:
            return todos
        return [r for r in todos if r.code != perms.ROLE_SUPERADMIN]

    # ── Mutaciones ────────────────────────────────────────────────────────

    @require_permission(perms.MANAGE_USERS)
    def create_usuario(
        self,
        username: str,
        full_name: str,
        role_id: int,
        password: str,
    ) -> None:
        """Crea un usuario nuevo. El actor sale de la sesión activa."""
        self._service.create_usuario(
            username=username,
            full_name=full_name,
            role_id=role_id,
            password=password,
            actor_user_id=self.session.user_id,
            actor_role_code=self.session.role_code,
        )

    @require_permission(perms.MANAGE_USERS)
    def update_usuario(
        self,
        user_id: int,
        full_name: str,
        role_id: int,
    ) -> None:
        """Actualiza nombre y rol de un usuario existente."""
        self._service.update_usuario(
            user_id=user_id,
            full_name=full_name,
            role_id=role_id,
            actor_user_id=self.session.user_id,
            actor_role_code=self.session.role_code,
        )

    @require_permission(perms.MANAGE_USERS)
    def reset_password(self, user_id: int, new_password: str) -> None:
        """Resetea la password de un usuario."""
        self._service.reset_password(
            user_id=user_id,
            new_password=new_password,
            actor_user_id=self.session.user_id,
            actor_role_code=self.session.role_code,
        )

    @require_permission(perms.MANAGE_USERS)
    def deactivate_usuario(self, user_id: int) -> None:
        """Desactiva un usuario (soft-delete)."""
        self._service.deactivate_usuario(user_id, actor_user_id=self.session.user_id)

    @require_permission(perms.MANAGE_USERS)
    def reactivate_usuario(self, user_id: int) -> None:
        """Reactiva un usuario desactivado."""
        self._service.reactivate_usuario(user_id, actor_user_id=self.session.user_id)

    @require_permission(perms.MANAGE_USERS)
    def unlock_usuario(self, user_id: int) -> None:
        """Desbloquea cuenta tras intentos fallidos."""
        self._service.unlock_usuario(user_id, actor_user_id=self.session.user_id)
