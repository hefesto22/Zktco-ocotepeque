"""Interfaces del repositorio de Cargo.

Gemelo de ``departamento_repository`` — mismo contrato y semántica, distinta
tabla subyacente. Se mantienen separados (en vez de una abstracción
compartida) porque cada uno tiene su propio CRUD en la UI y mezclarlos
acoplaría dos dimensiones del negocio que conviene mantener independientes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from core.models.cargo import Cargo


class ICargoReadRepository(ABC):
    """Operaciones de solo lectura sobre la tabla ``cargos``."""

    @abstractmethod
    def get_by_id(self, cargo_id: int) -> Optional[Cargo]:
        """Devuelve el cargo con ese id, o ``None`` si no existe."""

    @abstractmethod
    def get_by_nombre(self, nombre: str) -> Optional[Cargo]:
        """Devuelve el cargo con ese nombre, o ``None`` si no existe.

        La búsqueda es case-sensitive (consistente con el UNIQUE de la tabla).
        """

    @abstractmethod
    def list_all(self) -> List[Cargo]:
        """Devuelve todos los cargos (activos y archivados).

        Ordenados por ``nombre`` ascendente.
        """

    @abstractmethod
    def list_active(self) -> List[Cargo]:
        """Devuelve solo los cargos con ``is_active = True``.

        Ordenados por ``nombre`` ascendente.
        """


class ICargoWriteRepository(ABC):
    """Operaciones de escritura sobre la tabla ``cargos``."""

    @abstractmethod
    def create(self, cargo: Cargo) -> Cargo:
        """Inserta un cargo nuevo.

        Returns:
            Nueva instancia con el ``id`` ya asignado.

        Raises:
            sqlite3.IntegrityError: Si ``nombre`` ya existe (UNIQUE).
        """

    @abstractmethod
    def rename(self, cargo_id: int, nuevo_nombre: str) -> None:
        """Renombra un cargo existente.

        Raises:
            sqlite3.IntegrityError: Si ``nuevo_nombre`` colisiona con el UNIQUE.
        """

    @abstractmethod
    def archive(self, cargo_id: int) -> None:
        """Archiva el cargo (``is_active = False``)."""

    @abstractmethod
    def unarchive(self, cargo_id: int) -> None:
        """Reactiva el cargo (``is_active = True``)."""
