"""Tests del ConfiguracionController.

El controller es un wrapper delgado alrededor de ``CatalogoService``
(ya testeado en ``test_catalogo_service.py``), así que aquí validamos:

    - Cada método decorado delega al service con los argumentos correctos
      y pasa ``session.user_id`` como ``actor_user_id``.
    - Sin el permiso ``MANAGE_SETTINGS``, cualquier método lanza
      ``PermissionDeniedError`` sin tocar el service.

Usamos un stub manual del service (no MagicMock) para mantener los tests
explícitos y legibles — alineado con el patrón de ``test_main_controller``.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from core.models import permissions as perms
from core.models.cargo import Cargo
from core.models.departamento import Departamento
from core.services.errors import PermissionDeniedError
from core.services.permission_service import PermissionService
from core.services.session import Session
from ui.controllers.configuracion_controller import ConfiguracionController


# ── Stubs ─────────────────────────────────────────────────────────────────────


class _StubCatalogoService:
    """Stub manual del CatalogoService que registra cada llamada.

    No hereda del service real — el controller solo invoca métodos por
    nombre, así que un duck-typed stub es suficiente y evita arrastrar
    dependencias reales (repos, audit_logger) a los tests del controller.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._next_dep_id = 1
        self._next_cargo_id = 1

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    # Departamentos
    def list_departamentos(self, solo_activos: bool = True) -> List[Departamento]:
        self._record("list_departamentos", solo_activos=solo_activos)
        return [Departamento(id=1, nombre="Admin")]

    def create_departamento(self, nombre: str, actor_user_id: int) -> Departamento:
        self._record("create_departamento", nombre, actor_user_id)
        dep = Departamento(id=self._next_dep_id, nombre=nombre)
        self._next_dep_id += 1
        return dep

    def rename_departamento(
        self, departamento_id: int, nuevo_nombre: str, actor_user_id: int
    ) -> None:
        self._record("rename_departamento", departamento_id, nuevo_nombre, actor_user_id)

    def archive_departamento(self, departamento_id: int, actor_user_id: int) -> None:
        self._record("archive_departamento", departamento_id, actor_user_id)

    def unarchive_departamento(self, departamento_id: int, actor_user_id: int) -> None:
        self._record("unarchive_departamento", departamento_id, actor_user_id)

    # Cargos
    def list_cargos(self, solo_activos: bool = True) -> List[Cargo]:
        self._record("list_cargos", solo_activos=solo_activos)
        return [Cargo(id=1, nombre="Analista")]

    def create_cargo(self, nombre: str, actor_user_id: int) -> Cargo:
        self._record("create_cargo", nombre, actor_user_id)
        cargo = Cargo(id=self._next_cargo_id, nombre=nombre)
        self._next_cargo_id += 1
        return cargo

    def rename_cargo(self, cargo_id: int, nuevo_nombre: str, actor_user_id: int) -> None:
        self._record("rename_cargo", cargo_id, nuevo_nombre, actor_user_id)

    def archive_cargo(self, cargo_id: int, actor_user_id: int) -> None:
        self._record("archive_cargo", cargo_id, actor_user_id)

    def unarchive_cargo(self, cargo_id: int, actor_user_id: int) -> None:
        self._record("unarchive_cargo", cargo_id, actor_user_id)


def _session(permissions: set[str], user_id: int = 42) -> Session:
    return Session(
        user_id=user_id,
        username="test_user",
        role_id=1,
        role_code="TEST",
        permissions=frozenset(permissions),
    )


def _controller(
    permissions: set[str], user_id: int = 42
) -> Tuple[ConfiguracionController, _StubCatalogoService]:
    stub = _StubCatalogoService()
    ctrl = ConfiguracionController(
        session=_session(permissions, user_id=user_id),
        permission_service=PermissionService(),
        catalogo_service=stub,  # type: ignore[arg-type]  # duck-typed
    )
    return ctrl, stub


# ── Permiso correcto: delega + pasa user_id ──────────────────────────────────


def test_list_departamentos_delega_al_service() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS})
    result = ctrl.list_departamentos()
    assert len(result) == 1
    assert stub.calls == [("list_departamentos", (), {"solo_activos": True})]


def test_list_departamentos_con_archivados_pasa_flag() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS})
    ctrl.list_departamentos(solo_activos=False)
    assert stub.calls == [("list_departamentos", (), {"solo_activos": False})]


def test_create_departamento_pasa_user_id_como_actor() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS}, user_id=99)
    ctrl.create_departamento("RRHH")
    assert stub.calls == [("create_departamento", ("RRHH", 99), {})]


def test_rename_departamento_pasa_todos_los_args() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS}, user_id=7)
    ctrl.rename_departamento(5, "Nuevo nombre")
    assert stub.calls == [("rename_departamento", (5, "Nuevo nombre", 7), {})]


def test_archive_departamento_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS}, user_id=3)
    ctrl.archive_departamento(10)
    assert stub.calls == [("archive_departamento", (10, 3), {})]


def test_unarchive_departamento_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS}, user_id=3)
    ctrl.unarchive_departamento(10)
    assert stub.calls == [("unarchive_departamento", (10, 3), {})]


def test_create_cargo_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    ctrl.create_cargo("Contador")
    assert stub.calls == [("create_cargo", ("Contador", 11), {})]


def test_archive_cargo_delega() -> None:
    ctrl, stub = _controller({perms.MANAGE_SETTINGS}, user_id=11)
    ctrl.archive_cargo(8)
    assert stub.calls == [("archive_cargo", (8, 11), {})]


# ── Permiso faltante: lanza sin tocar el service ─────────────────────────────


def test_list_departamentos_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller(set())
    with pytest.raises(PermissionDeniedError) as exc:
        ctrl.list_departamentos()
    assert exc.value.permission == perms.MANAGE_SETTINGS
    assert stub.calls == []


def test_create_departamento_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller({perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.create_departamento("X")
    assert stub.calls == []


def test_archive_cargo_sin_permiso_lanza_permission_denied() -> None:
    ctrl, stub = _controller({perms.EXPORT_REPORTS})
    with pytest.raises(PermissionDeniedError):
        ctrl.archive_cargo(1)
    assert stub.calls == []


def test_reportes_role_no_puede_tocar_configuracion() -> None:
    """REPORTES tiene export_reports + view_export_history, no manage_settings."""
    ctrl, stub = _controller({perms.EXPORT_REPORTS, perms.VIEW_EXPORT_HISTORY})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_cargos()
    with pytest.raises(PermissionDeniedError):
        ctrl.create_departamento("X")
    assert stub.calls == []


def test_operador_role_no_puede_tocar_configuracion() -> None:
    """OPERADOR tiene run_zkteco_sync + view_attendance, no manage_settings."""
    ctrl, stub = _controller({perms.RUN_ZKTECO_SYNC, perms.VIEW_ATTENDANCE})
    with pytest.raises(PermissionDeniedError):
        ctrl.list_departamentos()
    assert stub.calls == []
