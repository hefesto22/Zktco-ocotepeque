"""Implementación SQLite del repositorio de Turno."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from core.models.turno import Turno
from core.repositories.turno_repository import (
    ITurnoReadRepository,
    ITurnoWriteRepository,
)
from infrastructure.database.connection import Database


def _row_to_turno(row: sqlite3.Row) -> Turno:
    """Mapea una fila de ``turnos`` al dataclass ``Turno``."""
    return Turno(
        id=row["id"],
        nombre=row["nombre"],
        hora_entrada=row["hora_entrada"],
        hora_salida=row["hora_salida"],
        minutos_descanso=row["minutos_descanso"],
        dias_semana=row["dias_semana"],
        cruza_medianoche=bool(row["cruza_medianoche"]),
        is_active=bool(row["is_active"]),
    )


class TurnoRepositorySQLite(ITurnoReadRepository, ITurnoWriteRepository):
    """Implementación SQLite — Opción A: una conexión por operación."""

    _SELECT_COLS = (
        "id, nombre, hora_entrada, hora_salida, minutos_descanso, "
        "dias_semana, cruza_medianoche, is_active"
    )

    def __init__(self, database: Database) -> None:
        """Inicializa el repo con el adaptador de BD inyectado."""
        self._db = database

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, turno_id: int) -> Optional[Turno]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos WHERE id = ?",
                (turno_id,),
            ).fetchone()
        return _row_to_turno(row) if row is not None else None

    def get_by_nombre(self, nombre: str) -> Optional[Turno]:
        with self._db.transaction() as conn:
            row: Optional[sqlite3.Row] = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos WHERE nombre = ?",
                (nombre,),
            ).fetchone()
        return _row_to_turno(row) if row is not None else None

    def list_all(self) -> List[Turno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos ORDER BY nombre ASC"
            ).fetchall()
        return [_row_to_turno(r) for r in rows]

    def list_active(self) -> List[Turno]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {self._SELECT_COLS} FROM turnos " "WHERE is_active = 1 ORDER BY nombre ASC"
            ).fetchall()
        return [_row_to_turno(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, turno: Turno) -> Turno:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO turnos "
                "(nombre, hora_entrada, hora_salida, minutos_descanso, "
                " dias_semana, cruza_medianoche, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    turno.nombre,
                    turno.hora_entrada,
                    turno.hora_salida,
                    turno.minutos_descanso,
                    turno.dias_semana,
                    1 if turno.cruza_medianoche else 0,
                    1 if turno.is_active else 0,
                ),
            )
            new_id = cursor.lastrowid
        return Turno(
            id=new_id,
            nombre=turno.nombre,
            hora_entrada=turno.hora_entrada,
            hora_salida=turno.hora_salida,
            minutos_descanso=turno.minutos_descanso,
            dias_semana=turno.dias_semana,
            cruza_medianoche=turno.cruza_medianoche,
            is_active=turno.is_active,
        )

    def update(self, turno: Turno) -> None:
        if turno.id is None:
            raise ValueError("No se puede actualizar un Turno sin id asignado.")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE turnos SET "
                "nombre = ?, hora_entrada = ?, hora_salida = ?, "
                "minutos_descanso = ?, dias_semana = ?, "
                "cruza_medianoche = ?, is_active = ? "
                "WHERE id = ?",
                (
                    turno.nombre,
                    turno.hora_entrada,
                    turno.hora_salida,
                    turno.minutos_descanso,
                    turno.dias_semana,
                    1 if turno.cruza_medianoche else 0,
                    1 if turno.is_active else 0,
                    turno.id,
                ),
            )

    def archive(self, turno_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE turnos SET is_active = 0 WHERE id = ?",
                (turno_id,),
            )

    def unarchive(self, turno_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE turnos SET is_active = 1 WHERE id = ?",
                (turno_id,),
            )
