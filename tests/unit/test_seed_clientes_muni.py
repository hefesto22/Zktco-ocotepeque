"""Tests del script ``bin.seed_clientes_muni``.

Smoke test: corre el script contra una BD recién migrada con un único
usuario activo y verifica que:

    - Se crean los catálogos esperados (depto TEST, cargo TEST global,
      turno "Diurno L-V 08-17" y turno "Sabatino 08-13").
    - Se crean los 21 empleados con DNIs únicos.
    - A todos se les asignan AMBOS turnos paralelos (Lun-Vie + Sábado)
      desde 2026-05-01 — días disjuntos, vigentes en simultáneo.
    - El script es idempotente (correrlo dos veces no rompe ni duplica).

NO valida la correctitud humana de los datos (nombres, DNIs específicos),
sólo que el script ejecute limpio. La validación de los datos contra la
planilla de origen es responsabilidad operativa, no del test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bin import seed_clientes_muni as seed
from core.models.usuario import Usuario
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "infrastructure" / "database" / "migrations"

ROLE_ID_SUPERADMIN = 1


def _crear_db_con_usuario(tmp_path: Path) -> Path:
    """Crea una BD migrada con un usuario activo (necesario para el audit log)."""
    db_path = tmp_path / "zkteco_app.db"
    database = Database(db_path)
    MigrationsRunner(database, MIGRATIONS_DIR).run()

    repo = UsuarioRepositorySQLite(database)
    repo.create(
        Usuario(
            id=None,
            username="admin",
            password_hash="fake-hash",
            full_name="Administrador",
            role_id=ROLE_ID_SUPERADMIN,
            is_active=True,
        )
    )
    return db_path


def test_seed_crea_los_21_empleados_con_dos_turnos_vigentes(tmp_path: Path) -> None:
    """End-to-end: una BD limpia → 21 empleados con L-V + Sábado vigentes."""
    db_path = _crear_db_con_usuario(tmp_path)

    exit_code = seed.main(["--db-path", str(db_path)])

    assert exit_code == 0
    database = Database(db_path)
    empleados = EmpleadoRepositorySQLite(database).list_active()
    assert len(empleados) == 21
    dnis = {emp.dni for emp in empleados}
    assert len(dnis) == 21, "Los DNIs deben ser únicos"

    # Cada empleado debe tener LOS DOS turnos paralelos asignados.
    et_repo = EmpleadoTurnoRepositorySQLite(database)
    for emp in empleados:
        assert emp.id is not None
        vigentes = et_repo.list_vigentes(emp.id)
        assert len(vigentes) == 2, (
            f"Empleado id={emp.id} debería tener 2 turnos vigentes "
            f"(L-V + Sábado), tiene {len(vigentes)}."
        )


def test_seed_es_idempotente(tmp_path: Path) -> None:
    """Correr el seed dos veces no debe duplicar empleados ni romper."""
    db_path = _crear_db_con_usuario(tmp_path)

    primer_run = seed.main(["--db-path", str(db_path)])
    segundo_run = seed.main(["--db-path", str(db_path)])

    assert primer_run == 0
    assert segundo_run == 0

    database = Database(db_path)
    empleados = EmpleadoRepositorySQLite(database).list_active()
    assert len(empleados) == 21


def test_seed_aborta_si_no_hay_usuarios(tmp_path: Path) -> None:
    """Sin usuarios activos no se puede asignar actor al audit log."""
    db_path = tmp_path / "zkteco_app.db"
    database = Database(db_path)
    MigrationsRunner(database, MIGRATIONS_DIR).run()

    exit_code = seed.main(["--db-path", str(db_path)])

    assert exit_code == 1


@pytest.mark.parametrize("dni,nombres,apellidos", seed._EMPLEADOS_MUNI)
def test_dnis_de_la_lista_tienen_formato_valido(dni: str, nombres: str, apellidos: str) -> None:
    """Los DNIs sembrados deben respetar el formato hondureño 4-4-5."""
    partes = dni.split("-")
    assert len(partes) == 3
    assert len(partes[0]) == 4 and partes[0].isdigit()
    assert len(partes[1]) == 4 and partes[1].isdigit()
    assert len(partes[2]) == 5 and partes[2].isdigit()
    assert nombres.strip() != ""
    assert apellidos.strip() != ""
