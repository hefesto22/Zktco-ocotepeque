"""Interfaces del repositorio de Usuario.

Se separan en ``IUsuarioReadRepository`` e ``IUsuarioWriteRepository``
siguiendo ISP (regla SOLID-I del proyecto): un consumidor que solo
necesita lectura (p. ej. la UI que lista usuarios) no debe depender
del contrato de escritura.

Ambas interfaces son ``abc.ABC`` y no ``typing.Protocol`` porque:
    - El proyecto usa inyección explícita en constructores (SOLID-D).
    - Es trivial crear mocks heredando y sobreescribiendo métodos.
    - mypy --strict valida firma exacta en las implementaciones.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.usuario import Usuario


class IUsuarioReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``usuarios``."""

    @abstractmethod
    def get_by_id(self, user_id: int) -> Optional[Usuario]:
        """Devuelve el usuario con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_username(self, username: str) -> Optional[Usuario]:
        """Devuelve el usuario con ese username, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def list_all(self) -> List[Usuario]:
        """Devuelve todos los usuarios ordenados por ``username`` ascendente."""

    @abstractmethod
    def count_by_role(self, role_id: int) -> int:
        """Devuelve cuántos usuarios tienen asignado ese rol.

        Útil para validar invariantes antes de mutar (p. ej. "¿ya hay un
        SUPERADMIN?" sin golpear el trigger SQL).
        """


class IUsuarioWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``usuarios``."""

    @abstractmethod
    def create(self, usuario: Usuario) -> Usuario:
        """Inserta un usuario nuevo.

        Args:
            usuario: Instancia con ``id=None``. ``created_at`` / ``updated_at``
                pueden ser ``None``; si lo son, el repo los rellena con el
                timestamp UTC actual.

        Returns:
            Nueva instancia ``Usuario`` con ``id``, ``created_at`` y
            ``updated_at`` ya asignados.

        Raises:
            sqlite3.IntegrityError: Si ``username`` ya existe o el trigger
                de SUPERADMIN único aborta la inserción.
        """

    @abstractmethod
    def update_login_state(
        self,
        user_id: int,
        failed_attempts: int,
        locked_until: Optional[str],
    ) -> None:
        """Actualiza solo los campos mutables del flujo de login.

        Se exponen únicamente estos dos campos (no un ``update`` genérico)
        para que AuthService no pueda, por error, escribir otros campos
        sensibles como ``password_hash`` o ``role_id``.
        """

    @abstractmethod
    def update_password_hash(self, user_id: int, new_hash: str) -> None:
        """Cambia el hash bcrypt del usuario.

        El llamador es responsable de haber validado la complejidad y
        generado el hash — el repo solo persiste.
        """

    @abstractmethod
    def delete(self, user_id: int) -> None:
        """Borra físicamente el usuario.

        Nota: para desactivar temporalmente sin perder historial, usar el
        flag ``is_active`` vía un update específico (aún no expuesto — se
        agregará cuando un caso de uso lo requiera, YAGNI).
        """
