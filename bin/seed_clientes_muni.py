"""Script de seed con los empleados REALES de la Municipalidad de Ocotepeque.

Se ejecuta una sola vez sobre la BD del .exe entregado al cliente, antes de
generar el .zip de instalación, para que la app llegue a la muni con los
21 empleados de la planilla ya precargados. En sitio:

    1. La unidad de informática enrola las huellas en el reloj K40,
       asignando a cada empleado un ZKTeco ID.
    2. Desde la UI de BioMuni se completa cada empleado: se le pega el
       ZKTeco ID enrolado, se lo mueve del depto "TEST" / cargo "TEST"
       a su depto/cargo real y se le ajusta el turno si trabaja distinto
       al "Diurno 08-17" por defecto.

Uso::

    python -m bin.seed_clientes_muni
    python -m bin.seed_clientes_muni --db-path "dist/zkteco/data/zkteco_app.db"

Catálogos que crea (si no existen ya):

    - Departamento "TEST"   (placeholder hasta que se creen los reales)
    - Cargo "TEST"           (placeholder hasta que se creen los reales)
    - Turno "Diurno 08-17"   (Lun-Vie, 08:00-17:00, 60min descanso)

Empleados sembrados: ver ``_EMPLEADOS_MUNI`` abajo. Los ``zkteco_id``
quedan en ``NULL`` hasta que la muni enrole en el reloj. La fecha de
ingreso unificada es ``2026-05-01`` (decisión operativa de Mauricio: la
fecha real de cada empleado se ajusta después manualmente).

DNIs marcados como ``1406-0000-90XXX`` son genéricos provisorios para
empleados cuyos DNIs no eran legibles en la planilla de origen — la muni
los corrige desde la UI cuando tenga el dato real.

Idempotente: si los catálogos o los empleados ya existen, los reusa.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import config
from core.repositories.audit_log_repository_sqlite import AuditLogRepositorySQLite
from core.repositories.cargo_repository_sqlite import CargoRepositorySQLite
from core.repositories.departamento_repository_sqlite import (
    DepartamentoRepositorySQLite,
)
from core.repositories.empleado_repository_sqlite import EmpleadoRepositorySQLite
from core.repositories.empleado_turno_repository_sqlite import (
    EmpleadoTurnoRepositorySQLite,
)
from core.repositories.turno_repository_sqlite import TurnoRepositorySQLite
from core.repositories.usuario_repository_sqlite import UsuarioRepositorySQLite
from core.services.audit_logger import AuditLogger
from core.services.catalogo_service import CatalogoService
from core.services.empleado_service import EmpleadoService
from core.services.errors import (
    DuplicateDNIError,
    DuplicateNombreError,
    DuplicateTurnoNombreError,
    DuplicateZktecoIdError,
    TurnoYaAsignadoError,
)
from core.services.turno_service import TurnoService
from core.models.turno import DIAS_LABORALES
from infrastructure.database.connection import Database
from infrastructure.database.migrations_runner import MigrationsRunner

# ── Catálogos ────────────────────────────────────────────────────────────────
_DEPARTAMENTO_PLACEHOLDER = "TEST"
_CARGO_PLACEHOLDER = "TEST"
_TURNO_NOMBRE = "Diurno 08-17"
_TURNO_HORA_ENTRADA = "08:00"
_TURNO_HORA_SALIDA = "17:00"
_TURNO_DESCANSO_MIN = 60
_TURNO_DIAS = DIAS_LABORALES  # bitmask Lun-Vie
_FECHA_INGRESO = "2026-05-01"

# ── Empleados reales de la muni ──────────────────────────────────────────────
# Tupla: (dni, nombres, apellidos). Comentario lateral con el correlativo
# de la planilla original para trazabilidad — no se persiste.
#
# DNIs ``1406-0000-90XXX`` son provisorios (no legibles en la planilla);
# la muni los corrige desde la UI cuando tenga el dato real.
_EMPLEADOS_MUNI = [
    ("1406-0000-90002", "Mirna Yamileth", "Maldonado Maldonado"),  # 02
    ("1406-1984-00008", "Elsy Roel", "Posadas López"),  # 03
    ("1406-2001-00149", "María del Carmen", "Coto Rivas"),  # 04
    ("1406-1999-00098", "Ilsy Nohelia", "Garza Marín"),  # 05
    ("1406-1989-00105", "Darwin David", "Aquino Maldonado"),  # 06
    ("1406-2000-00130", "Kenia Beatris", "Rosa Ponce"),  # 07
    ("1406-2002-00073", "Patrik Anderson", "Orellana Deras"),  # 08
    ("1406-2000-00111", "Beyny Arnoldo", "Moran García"),  # 09
    ("1406-2003-00067", "Carmen Concepción", "Posadas Jaco"),  # 10
    ("1406-2006-00095", "Sammy Alberto", "Oliva Molina"),  # 11 (autor del aporte)
    ("1406-1997-00180", "Edgar Andony", "Jaco Garza"),  # 12
    ("1406-1998-00186", "Danessy Maybeli", "Posadas Yanes"),  # 13
    ("1406-2007-00045", "María del Carmen", "Guzmán Gutiérrez"),  # 14
    ("1406-0000-90015", "Servin Alessandro", "Moran Aquino"),  # 15 (DNI provisorio)
    ("1406-0000-90016", "Rigoberto", "Posadas"),  # 16 (DNI provisorio)
    ("1406-2002-00003", "Henry Josue", "Torres Dubon"),  # 17
    ("1406-1999-00048", "David Antonio", "Deras Santos"),  # 18
    ("1406-1993-00072", "Arnol Gabriel", "Posadas Chinchilla"),  # 19
    ("1406-1988-00176", "Melvin Yobani", "López Reyes"),  # 20
    ("1406-2002-00071", "Gloria Mireya", "Canales Rodriguez"),  # 21
    ("1406-1979-00130", "Claudio Lindolfo", "Arriaza Dubon"),  # 22
]


def main(argv: Optional[list[str]] = None) -> int:
    """Ejecuta el seed. Devuelve el exit code.

    Args:
        argv: Lista de argumentos (sin ``argv[0]``). Si es ``None`` usa
            ``sys.argv[1:]``. Permite pasar args programáticamente desde
            tests.
    """
    args = _parse_args(argv)
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)

    db_path = _resolver_db_path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(db_path)
    MigrationsRunner(database, config.MIGRATIONS_DIR).run()

    actor_id = _resolver_actor_user_id(database)
    if actor_id is None:
        print(
            f"✗ No se encontró ningún usuario activo en {db_path}."
            "\n  Corré primero el setup_wizard contra esa misma BD:"
            "\n    python -m bin.setup_wizard"
        )
        return 1

    print("═══════════════════════════════════════════════════════════════")
    print("  Seed de empleados de la Municipalidad de Ocotepeque — BioMuni")
    print(f"  BD: {db_path}")
    print(f"  Actor (audit): user_id={actor_id}")
    print(f"  Empleados a sembrar: {len(_EMPLEADOS_MUNI)}")
    print("═══════════════════════════════════════════════════════════════")

    catalogo_svc, turno_svc, empleado_svc = _build_services(database)
    creados: dict[str, int] = {
        "departamento": 0,
        "cargo": 0,
        "turno": 0,
        "empleado": 0,
        "asignacion_turno": 0,
    }

    departamento_id = _ensure_departamento(catalogo_svc, actor_id, creados)
    cargo_id = _ensure_cargo(catalogo_svc, actor_id, creados)
    turno_id = _ensure_turno(turno_svc, actor_id, creados)

    for dni, nombres, apellidos in _EMPLEADOS_MUNI:
        emp_id = _ensure_empleado(
            empleado_svc,
            dni=dni,
            nombres=nombres,
            apellidos=apellidos,
            departamento_id=departamento_id,
            cargo_id=cargo_id,
            actor_id=actor_id,
            creados=creados,
        )
        if emp_id is not None:
            _ensure_asignacion_turno(
                empleado_svc,
                empleado_id=emp_id,
                turno_id=turno_id,
                actor_id=actor_id,
                creados=creados,
            )

    print()
    print("───────────────────────────────────────────────────────────────")
    print("  Resumen del seed:")
    print(f"    · Departamentos creados: {creados['departamento']}")
    print(f"    · Cargos creados:        {creados['cargo']}")
    print(f"    · Turnos creados:        {creados['turno']}")
    print(f"    · Empleados creados:     {creados['empleado']}")
    print(f"    · Asignaciones de turno: {creados['asignacion_turno']}")
    print("───────────────────────────────────────────────────────────────")
    print("  Listo. Los empleados quedan con depto/cargo TEST y turno")
    print("  Diurno 08-17 — la muni los reasigna desde la UI cuando")
    print("  enrole las huellas en el reloj K40.")
    return 0


# ── Argumentos y resolución de BD ────────────────────────────────────────────


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parsea ``--db-path`` y devuelve el ``Namespace``."""
    parser = argparse.ArgumentParser(
        prog="bin.seed_clientes_muni",
        description=(
            "Siembra los 21 empleados reales de la Municipalidad de "
            "Ocotepeque en la BD indicada. Idempotente."
        ),
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=(
            "Ruta al archivo SQLite a sembrar. Útil para apuntar a la BD "
            "del .exe empaquetado (p.ej. dist/zkteco/data/zkteco_app.db)."
        ),
    )
    return parser.parse_args(argv)


def _resolver_db_path(explicit: Optional[str]) -> Path:
    """Devuelve el ``Path`` absoluto de la BD a sembrar."""
    if explicit is not None:
        return Path(explicit).resolve()
    return config.DATABASE_PATH


# ── Composition root ─────────────────────────────────────────────────────────


def _build_services(
    database: Database,
) -> tuple[CatalogoService, TurnoService, EmpleadoService]:
    """Compone los 3 services que el seed necesita con sus repos reales."""
    audit = AuditLogger(AuditLogRepositorySQLite(database))
    dep_repo = DepartamentoRepositorySQLite(database)
    cargo_repo = CargoRepositorySQLite(database)
    emp_repo = EmpleadoRepositorySQLite(database)
    emp_turno_repo = EmpleadoTurnoRepositorySQLite(database)
    turno_repo = TurnoRepositorySQLite(database)

    catalogo_svc = CatalogoService(
        dep_read=dep_repo,
        dep_write=dep_repo,
        cargo_read=cargo_repo,
        cargo_write=cargo_repo,
        empleado_read=emp_repo,
        audit_logger=audit,
    )
    turno_svc = TurnoService(
        turno_read=turno_repo,
        turno_write=turno_repo,
        audit_logger=audit,
    )
    empleado_svc = EmpleadoService(
        empleado_read=emp_repo,
        empleado_write=emp_repo,
        empleado_turno_read=emp_turno_repo,
        empleado_turno_write=emp_turno_repo,
        departamento_read=dep_repo,
        cargo_read=cargo_repo,
        turno_read=turno_repo,
        audit_logger=audit,
    )
    return catalogo_svc, turno_svc, empleado_svc


def _resolver_actor_user_id(database: Database) -> Optional[int]:
    """Devuelve el user_id del primer usuario activo, o ``None``."""
    repo = UsuarioRepositorySQLite(database)
    for usuario in repo.list_all():
        if usuario.id is not None and usuario.is_active:
            return usuario.id
    return None


# ── Helpers idempotentes ─────────────────────────────────────────────────────


def _ensure_departamento(
    catalogo_svc: CatalogoService, actor_id: int, creados: dict[str, int]
) -> int:
    """Crea el departamento placeholder si no existe; devuelve su id."""
    for dep in catalogo_svc.list_departamentos(solo_activos=False):
        if dep.nombre == _DEPARTAMENTO_PLACEHOLDER and dep.id is not None:
            print(f'• Departamento "{_DEPARTAMENTO_PLACEHOLDER}" ya existe (id={dep.id}).')
            return dep.id
    try:
        creado = catalogo_svc.create_departamento(_DEPARTAMENTO_PLACEHOLDER, actor_id)
    except DuplicateNombreError:
        for dep in catalogo_svc.list_departamentos(solo_activos=False):
            if dep.nombre == _DEPARTAMENTO_PLACEHOLDER and dep.id is not None:
                return dep.id
        raise
    assert creado.id is not None
    creados["departamento"] += 1
    print(f'✓ Departamento "{_DEPARTAMENTO_PLACEHOLDER}" creado (id={creado.id}).')
    return creado.id


def _ensure_cargo(catalogo_svc: CatalogoService, actor_id: int, creados: dict[str, int]) -> int:
    """Crea el cargo placeholder si no existe; devuelve su id."""
    for cargo in catalogo_svc.list_cargos(solo_activos=False):
        if cargo.nombre == _CARGO_PLACEHOLDER and cargo.id is not None:
            print(f'• Cargo "{_CARGO_PLACEHOLDER}" ya existe (id={cargo.id}).')
            return cargo.id
    try:
        creado = catalogo_svc.create_cargo(_CARGO_PLACEHOLDER, actor_id)
    except DuplicateNombreError:
        for cargo in catalogo_svc.list_cargos(solo_activos=False):
            if cargo.nombre == _CARGO_PLACEHOLDER and cargo.id is not None:
                return cargo.id
        raise
    assert creado.id is not None
    creados["cargo"] += 1
    print(f'✓ Cargo "{_CARGO_PLACEHOLDER}" creado (id={creado.id}).')
    return creado.id


def _ensure_turno(turno_svc: TurnoService, actor_id: int, creados: dict[str, int]) -> int:
    """Crea el turno Diurno 08-17 si no existe; devuelve su id."""
    for turno in turno_svc.list_turnos(solo_activos=False):
        if turno.nombre == _TURNO_NOMBRE and turno.id is not None:
            print(f'• Turno "{_TURNO_NOMBRE}" ya existe (id={turno.id}).')
            return turno.id
    try:
        creado = turno_svc.create_turno(
            nombre=_TURNO_NOMBRE,
            hora_entrada=_TURNO_HORA_ENTRADA,
            hora_salida=_TURNO_HORA_SALIDA,
            minutos_descanso=_TURNO_DESCANSO_MIN,
            dias_semana=_TURNO_DIAS,
            actor_user_id=actor_id,
        )
    except DuplicateTurnoNombreError:
        for turno in turno_svc.list_turnos(solo_activos=False):
            if turno.nombre == _TURNO_NOMBRE and turno.id is not None:
                return turno.id
        raise
    assert creado.id is not None
    creados["turno"] += 1
    print(f'✓ Turno "{_TURNO_NOMBRE}" creado (id={creado.id}).')
    return creado.id


def _ensure_empleado(
    empleado_svc: EmpleadoService,
    *,
    dni: str,
    nombres: str,
    apellidos: str,
    departamento_id: int,
    cargo_id: int,
    actor_id: int,
    creados: dict[str, int],
) -> Optional[int]:
    """Crea el empleado si no existe; devuelve su id o None ante conflicto."""
    existente = empleado_svc.get_by_dni(dni) if hasattr(empleado_svc, "get_by_dni") else None
    if existente is not None and existente.id is not None:
        print(f'• Empleado "{nombres} {apellidos}" (DNI {dni}) ' f"ya existe (id={existente.id}).")
        return existente.id
    try:
        creado = empleado_svc.create_empleado(
            dni=dni,
            nombres=nombres,
            apellidos=apellidos,
            departamento_id=departamento_id,
            cargo_id=cargo_id,
            fecha_ingreso=_FECHA_INGRESO,
            actor_user_id=actor_id,
            zkteco_id=None,  # se asigna en sitio cuando se enrole en el K40
        )
    except DuplicateDNIError:
        print(f"• Empleado con DNI {dni} ya existe — se omite.")
        return None
    except DuplicateZktecoIdError:
        # Defensivo: zkteco_id viene None, no debería entrar acá.
        print(
            f'⚠ ZKTeco ID conflicto inesperado en "{nombres} {apellidos}". '
            f"Verificá manualmente."
        )
        return None
    assert creado.id is not None
    creados["empleado"] += 1
    print(f'✓ Empleado "{nombres} {apellidos}" creado (id={creado.id}, DNI {dni}).')
    return creado.id


def _ensure_asignacion_turno(
    empleado_svc: EmpleadoService,
    *,
    empleado_id: int,
    turno_id: int,
    actor_id: int,
    creados: dict[str, int],
) -> None:
    """Asigna el turno al empleado si aún no tiene uno vigente."""
    vigente = empleado_svc.get_turno_vigente(empleado_id)
    if vigente is not None:
        print(f"  · Empleado id={empleado_id} ya tiene turno vigente " f"(id={vigente.turno_id}).")
        return
    try:
        empleado_svc.asignar_turno(
            empleado_id=empleado_id,
            turno_id=turno_id,
            fecha_inicio=_FECHA_INGRESO,
            actor_user_id=actor_id,
        )
    except TurnoYaAsignadoError:
        return
    creados["asignacion_turno"] += 1
    print(f"  · Turno asignado a empleado id={empleado_id} desde {_FECHA_INGRESO}.")


if __name__ == "__main__":
    sys.exit(main())
