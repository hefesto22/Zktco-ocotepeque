"""Modelo de dominio: Cargo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Cargo:
    """Cargo o puesto de la municipalidad (catálogo).

    Diseño: gemelo de ``Departamento`` — mismo esquema y semántica, distinta
    dimensión del negocio. Se mantiene en una tabla aparte para permitir
    CRUD independiente y evitar acoplamiento accidental entre las dos
    taxonomías.

    Archivado vía ``is_active`` (Decisión 6 del PRD de Fase 2 — sin DELETE
    físico). Nombre único a nivel BD.

    Atributos:
        id: PK en la tabla ``cargos``. ``None`` si aún no fue persistido.
        nombre: Texto único, case-sensitive. Visible al usuario.
        is_active: ``False`` archiva el cargo — deja de aparecer en
            dropdowns de alta/edición pero preserva FKs históricas.
    """

    id: Optional[int]
    nombre: str
    is_active: bool = True
