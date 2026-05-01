"""Servicio de empleados: CRUD + ciclo de vida + asignación de turnos.

Orquesta tres responsabilidades cohesivas bajo un mismo empleado:

    1. CRUD de datos demográficos y contractuales.
    2. Baja / reactivación con trazabilidad.
    3. Asignación y cambio de turno vigente (Opción A aprobada en Sub-2.3:
       las relaciones empleado↔turno viven en el servicio que "dueña"
       del empleado).

Reglas de negocio:

    - DNI hondureño con regex ``XXXX-XXXX-XXXXX`` — único por empleado.
    - ``zkteco_id`` único cuando se asigna; múltiples ``None`` son OK.
    - Departamento y cargo deben existir y estar ACTIVOS al crear o al
      actualizar (no se puede mover un empleado a un catálogo archivado).
    - Deactivate requiere ``motivo_baja`` ∈ ``MotivoBaja`` y, si el motivo
      es ``OTRO``, ``nota_baja`` obligatoria. Además, cierra
      automáticamente la asignación de turno vigente con
      ``fecha_fin = fecha_baja``.
    - Reactivate NO restaura asignación de turno — el caller debe llamar
      ``asignar_turno`` explícitamente después. Rationale: la realidad
      al reactivar es que el empleado suele venir con otro turno o ajuste.
    - ``asignar_turno`` solo funciona si el empleado no tiene vigente;
      si lo tiene → ``TurnoYaAsignadoError``.
    - ``cambiar_turno`` solo funciona si el empleado tiene vigente;
      si no → ``SinTurnoVigenteError``. ``fecha_fin_vigente`` se calcula
      como ``fecha_inicio_nueva - 1 día`` y se valida que sea
      estrictamente posterior a ``fecha_inicio`` de la vigente.

Diseño (SOLID):
    - S: gestiona el empleado y TODO lo que cuelga directamente de él.
    - D: recibe 7 dependencias inyectadas (repos + audit_logger) —
         ningún ``import`` de infraestructura concreta.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import List, Optional

from core.models.empleado import ALL_MOTIVOS_BAJA, Empleado
from core.models.empleado_turno import EmpleadoTurno
from core.repositories.cargo_repository import ICargoReadRepository
from core.repositories.departamento_repository import IDepartamentoReadRepository
from core.repositories.empleado_repository import (
    IEmpleadoReadRepository,
    IEmpleadoWriteRepository,
)
from core.repositories.empleado_turno_repository import (
    IEmpleadoTurnoReadRepository,
    IEmpleadoTurnoWriteRepository,
)
from core.repositories.turno_repository import ITurnoReadRepository
from core.services.audit_logger import AuditLogger
from core.services.errors import (
    CatalogoNotFoundError,
    DuplicateDNIError,
    DuplicateZktecoIdError,
    EmpleadoAlreadyActiveError,
    EmpleadoAlreadyInactiveError,
    EmpleadoNotFoundError,
    InvalidDateError,
    InvalidMotivoBajaError,
    MissingRequiredFieldError,
    SinTurnoVigenteError,
    TurnoBitmaskSolapadoError,
    TurnoInactiveError,
    TurnoNotFoundError,
)
from core.services.validators import validate_dni, validate_fecha_iso


class EmpleadoService:
    """Servicio de gestión completa del empleado."""

    def __init__(
        self,
        empleado_read: IEmpleadoReadRepository,
        empleado_write: IEmpleadoWriteRepository,
        empleado_turno_read: IEmpleadoTurnoReadRepository,
        empleado_turno_write: IEmpleadoTurnoWriteRepository,
        departamento_read: IDepartamentoReadRepository,
        cargo_read: ICargoReadRepository,
        turno_read: ITurnoReadRepository,
        audit_logger: AuditLogger,
    ) -> None:
        """Inicializa el servicio con sus 8 dependencias inyectadas."""
        self._emp_read = empleado_read
        self._emp_write = empleado_write
        self._et_read = empleado_turno_read
        self._et_write = empleado_turno_write
        self._dep_read = departamento_read
        self._cargo_read = cargo_read
        self._turno_read = turno_read
        self._audit = audit_logger
        self._log = logging.getLogger(self.__class__.__name__)

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_empleados(self, solo_activos: bool = True) -> List[Empleado]:
        """Devuelve la lista de empleados (activos por default)."""
        if solo_activos:
            return self._emp_read.list_active()
        return self._emp_read.list_all()

    def list_by_departamento(
        self, departamento_id: int, solo_activos: bool = True
    ) -> List[Empleado]:
        """Devuelve los empleados de un departamento."""
        return self._emp_read.list_by_departamento(departamento_id, solo_activos)

    def list_by_cargo(self, cargo_id: int, solo_activos: bool = True) -> List[Empleado]:
        """Devuelve los empleados de un cargo."""
        return self._emp_read.list_by_cargo(cargo_id, solo_activos)

    def get_empleado(self, empleado_id: int) -> Empleado:
        """Devuelve un empleado por id.

        Raises:
            EmpleadoNotFoundError: Si no existe el id.
        """
        emp = self._emp_read.get_by_id(empleado_id)
        if emp is None:
            raise EmpleadoNotFoundError(empleado_id)
        return emp

    def get_by_dni(self, dni: str) -> Optional[Empleado]:
        """Devuelve el empleado con ese DNI, o ``None``.

        No lanza error si no existe — este método está pensado para
        búsquedas desde la UI (si el DNI no está, el usuario quiere
        crear uno nuevo).
        """
        dni_norm = validate_dni(dni)
        return self._emp_read.get_by_dni(dni_norm)

    # ── Writes: CRUD básico ───────────────────────────────────────────────

    def create_empleado(
        self,
        dni: str,
        nombres: str,
        apellidos: str,
        departamento_id: int,
        cargo_id: int,
        fecha_ingreso: str,
        actor_user_id: int,
        telefono: Optional[str] = None,
        email: Optional[str] = None,
        zkteco_id: Optional[int] = None,
    ) -> Empleado:
        """Crea un empleado nuevo con validaciones completas.

        Raises:
            MissingRequiredFieldError: Nombre / apellido vacíos.
            InvalidDNIError, InvalidDateError: Formato inválido.
            CatalogoNotFoundError: Depto o cargo no existen o están
                archivados.
            DuplicateDNIError, DuplicateZktecoIdError: Colisión.
        """
        dni_norm = validate_dni(dni)
        nombres_t = self._require_trim(nombres, "nombres")
        apellidos_t = self._require_trim(apellidos, "apellidos")
        validate_fecha_iso(fecha_ingreso, "fecha_ingreso")
        self._validar_catalogos_activos(departamento_id, cargo_id)
        self._validar_dni_unico(dni_norm, excepto_id=None)
        self._validar_zkteco_unico(zkteco_id, excepto_id=None)

        creado = self._emp_write.create(
            Empleado(
                id=None,
                dni=dni_norm,
                nombres=nombres_t,
                apellidos=apellidos_t,
                departamento_id=departamento_id,
                cargo_id=cargo_id,
                fecha_ingreso=fecha_ingreso,
                telefono=telefono,
                email=email,
                zkteco_id=zkteco_id,
                is_active=True,
            )
        )
        assert creado.id is not None
        self._audit.log(
            action="empleado_created",
            user_id=actor_user_id,
            details=f'{{"id": {creado.id}, "dni": "{dni_norm}"}}',
        )
        self._log.info("Empleado creado: id=%s dni=%s", creado.id, dni_norm)
        return creado

    def update_empleado(
        self,
        empleado_id: int,
        dni: str,
        nombres: str,
        apellidos: str,
        departamento_id: int,
        cargo_id: int,
        fecha_ingreso: str,
        actor_user_id: int,
        telefono: Optional[str] = None,
        email: Optional[str] = None,
        zkteco_id: Optional[int] = None,
    ) -> None:
        """Actualiza los campos editables del empleado.

        No toca ``is_active`` ni los campos de baja — para eso están
        ``deactivate_empleado`` / ``reactivate_empleado``.

        Raises:
            EmpleadoNotFoundError: ``empleado_id`` no existe.
            (Y las mismas validaciones que ``create_empleado``.)
        """
        existente = self.get_empleado(empleado_id)
        dni_norm = validate_dni(dni)
        nombres_t = self._require_trim(nombres, "nombres")
        apellidos_t = self._require_trim(apellidos, "apellidos")
        validate_fecha_iso(fecha_ingreso, "fecha_ingreso")
        self._validar_catalogos_activos(departamento_id, cargo_id)
        self._validar_dni_unico(dni_norm, excepto_id=empleado_id)
        self._validar_zkteco_unico(zkteco_id, excepto_id=empleado_id)

        self._emp_write.update(
            Empleado(
                id=empleado_id,
                dni=dni_norm,
                nombres=nombres_t,
                apellidos=apellidos_t,
                departamento_id=departamento_id,
                cargo_id=cargo_id,
                fecha_ingreso=fecha_ingreso,
                telefono=telefono,
                email=email,
                zkteco_id=zkteco_id,
                is_active=existente.is_active,
            )
        )
        self._audit.log(
            action="empleado_updated",
            user_id=actor_user_id,
            details=f'{{"id": {empleado_id}, "dni": "{dni_norm}"}}',
        )
        self._log.info("Empleado actualizado: id=%s", empleado_id)

    # ── Writes: ciclo de vida (baja / reactivación) ───────────────────────

    def deactivate_empleado(
        self,
        empleado_id: int,
        fecha_baja: str,
        motivo_baja: str,
        actor_user_id: int,
        nota_baja: Optional[str] = None,
    ) -> None:
        """Archiva el empleado con trazabilidad y cierra su turno vigente.

        Reglas:
            - Empleado debe estar activo.
            - ``motivo_baja`` ∈ ``MotivoBaja``.
            - Si ``motivo_baja == 'OTRO'``, ``nota_baja`` es obligatoria.
            - Si hay turno vigente, se cierra con ``fecha_fin = fecha_baja``
              ANTES del UPDATE de baja. Si el repo de empleado_turno
              rechaza (fecha_fin < fecha_inicio), se propaga el error
              sin tocar ``empleados``.

        Raises:
            EmpleadoNotFoundError, EmpleadoAlreadyInactiveError,
            InvalidMotivoBajaError, MissingRequiredFieldError,
            InvalidDateError.
        """
        empleado = self.get_empleado(empleado_id)
        if not empleado.is_active:
            raise EmpleadoAlreadyInactiveError(empleado_id)
        validate_fecha_iso(fecha_baja, "fecha_baja")
        if motivo_baja not in ALL_MOTIVOS_BAJA:
            raise InvalidMotivoBajaError(motivo_baja)
        if motivo_baja == "OTRO" and not (nota_baja and nota_baja.strip()):
            raise MissingRequiredFieldError("nota_baja")
        nota_final = nota_baja.strip() if nota_baja else None

        # Cerrar turno vigente ANTES de la baja para que si el repo
        # rechaza la fecha (CHECK de fecha_fin >= fecha_inicio), el
        # estado de `empleados` quede intacto.
        vigente = self._et_read.get_vigente(empleado_id)
        if vigente is not None:
            self._et_write.cerrar_vigente(empleado_id, fecha_baja)

        self._emp_write.deactivate(empleado_id, fecha_baja, motivo_baja, nota_final)
        self._audit.log(
            action="empleado_deactivated",
            user_id=actor_user_id,
            details=(
                f'{{"id": {empleado_id}, ' f'"motivo": "{motivo_baja}", "fecha": "{fecha_baja}"}}'
            ),
        )
        self._log.info(
            "Empleado archivado: id=%s motivo=%s fecha=%s",
            empleado_id,
            motivo_baja,
            fecha_baja,
        )

    def reactivate_empleado(self, empleado_id: int, actor_user_id: int) -> None:
        """Reactiva un empleado archivado.

        NO restaura asignación de turno — el caller debe invocar
        ``asignar_turno`` explícitamente después.

        Raises:
            EmpleadoNotFoundError, EmpleadoAlreadyActiveError.
        """
        empleado = self.get_empleado(empleado_id)
        if empleado.is_active:
            raise EmpleadoAlreadyActiveError(empleado_id)
        self._emp_write.reactivate(empleado_id)
        self._audit.log(
            action="empleado_reactivated",
            user_id=actor_user_id,
            details=f'{{"id": {empleado_id}}}',
        )
        self._log.info("Empleado reactivado: id=%s", empleado_id)

    # ── Writes: asignación de turnos ──────────────────────────────────────

    def asignar_turno(
        self,
        empleado_id: int,
        turno_id: int,
        fecha_inicio: str,
        actor_user_id: int,
    ) -> EmpleadoTurno:
        """Asigna un turno a un empleado.

        Sub-3.2.B: si el empleado ya tiene asignaciones vigentes, esta
        función las acepta SI los días de la semana del turno nuevo no
        se solapan con ninguna vigente. Esto permite modelar casos
        como "lun-vie 8-17 + sábado 8-13" como dos asignaciones
        paralelas.

        Si el bitmask del nuevo se solapa con cualquiera de las
        vigentes, se rechaza con ``TurnoBitmaskSolapadoError``.

        Raises:
            EmpleadoNotFoundError, EmpleadoAlreadyInactiveError,
            TurnoNotFoundError, TurnoInactiveError,
            TurnoBitmaskSolapadoError, InvalidDateError.
        """
        empleado = self.get_empleado(empleado_id)
        if not empleado.is_active:
            raise EmpleadoAlreadyInactiveError(empleado_id)
        self._validar_turno_activo(turno_id)
        validate_fecha_iso(fecha_inicio, "fecha_inicio")

        self._validar_bitmask_no_solapado(empleado_id, turno_id)

        asignacion = self._et_write.asignar(empleado_id, turno_id, fecha_inicio)
        self._audit.log(
            action="turno_asignado",
            user_id=actor_user_id,
            details=(
                f'{{"empleado_id": {empleado_id}, '
                f'"turno_id": {turno_id}, "desde": "{fecha_inicio}"}}'
            ),
        )
        self._log.info(
            "Turno asignado: emp=%s turno=%s desde=%s",
            empleado_id,
            turno_id,
            fecha_inicio,
        )
        return asignacion

    def cambiar_turno(
        self,
        empleado_id: int,
        turno_nuevo_id: int,
        fecha_inicio_nueva: str,
        actor_user_id: int,
    ) -> EmpleadoTurno:
        """Cambia el turno vigente de un empleado de forma atómica.

        Calcula ``fecha_fin_vigente = fecha_inicio_nueva - 1 día`` y
        valida que sea estrictamente posterior a ``fecha_inicio`` de la
        asignación vigente.

        Raises:
            EmpleadoNotFoundError, EmpleadoAlreadyInactiveError,
            TurnoNotFoundError, TurnoInactiveError, SinTurnoVigenteError,
            InvalidDateError.
        """
        empleado = self.get_empleado(empleado_id)
        if not empleado.is_active:
            raise EmpleadoAlreadyInactiveError(empleado_id)
        self._validar_turno_activo(turno_nuevo_id)
        fecha_inicio_nueva_d = validate_fecha_iso(fecha_inicio_nueva, "fecha_inicio_nueva")

        vigente = self._et_read.get_vigente(empleado_id)
        if vigente is None:
            raise SinTurnoVigenteError(empleado_id)

        fecha_inicio_vigente_d = validate_fecha_iso(vigente.fecha_inicio, "fecha_inicio_vigente")
        if fecha_inicio_nueva_d <= fecha_inicio_vigente_d:
            raise InvalidDateError(
                fecha_inicio_nueva,
                "fecha_inicio_nueva (debe ser posterior al inicio del turno vigente)",
            )
        fecha_fin_vigente = (fecha_inicio_nueva_d - timedelta(days=1)).isoformat()

        nueva = self._et_write.cerrar_vigente_y_asignar(
            empleado_id=empleado_id,
            turno_id_nuevo=turno_nuevo_id,
            fecha_fin_vigente=fecha_fin_vigente,
            fecha_inicio_nueva=fecha_inicio_nueva,
        )
        self._audit.log(
            action="turno_cambiado",
            user_id=actor_user_id,
            details=(
                f'{{"empleado_id": {empleado_id}, '
                f'"turno_nuevo_id": {turno_nuevo_id}, '
                f'"desde": "{fecha_inicio_nueva}"}}'
            ),
        )
        self._log.info(
            "Turno cambiado: emp=%s turno_nuevo=%s desde=%s",
            empleado_id,
            turno_nuevo_id,
            fecha_inicio_nueva,
        )
        return nueva

    def get_turno_vigente(self, empleado_id: int) -> Optional[EmpleadoTurno]:
        """Devuelve la asignación de turno vigente del empleado, o ``None``."""
        return self._et_read.get_vigente(empleado_id)

    def get_historial_turnos(self, empleado_id: int) -> List[EmpleadoTurno]:
        """Devuelve todo el historial de asignaciones del empleado.

        Ordenado por ``fecha_inicio`` descendente.
        """
        return self._et_read.list_historial(empleado_id)

    # ── Helpers privados ──────────────────────────────────────────────────

    @staticmethod
    def _require_trim(valor: str, campo: str) -> str:
        """Devuelve ``valor.strip()`` o lanza ``MissingRequiredFieldError``."""
        if valor is None or not valor.strip():
            raise MissingRequiredFieldError(campo)
        return valor.strip()

    def _validar_catalogos_activos(self, departamento_id: int, cargo_id: int) -> None:
        """Valida que depto y cargo existan, estén activos y sean compatibles.

        Sub-3.2.A: si el cargo tiene ``departamento_id`` distinto del
        departamento elegido, se rechaza — un cargo específico de
        Tesorería no puede asignarse a un empleado de Obras Públicas.
        Los cargos globales (``departamento_id IS NULL``) son válidos
        para cualquier departamento.
        """
        dep = self._dep_read.get_by_id(departamento_id)
        if dep is None or not dep.is_active:
            raise CatalogoNotFoundError("departamento activo", departamento_id)
        cargo = self._cargo_read.get_by_id(cargo_id)
        if cargo is None or not cargo.is_active:
            raise CatalogoNotFoundError("cargo activo", cargo_id)
        if cargo.departamento_id is not None and cargo.departamento_id != departamento_id:
            raise CatalogoNotFoundError(
                f"cargo válido para departamento {departamento_id}", cargo_id
            )

    def _validar_turno_activo(self, turno_id: int) -> None:
        """Valida que el turno exista y esté activo."""
        turno = self._turno_read.get_by_id(turno_id)
        if turno is None:
            raise TurnoNotFoundError(turno_id)
        if not turno.is_active:
            raise TurnoInactiveError(turno_id)

    def _validar_bitmask_no_solapado(self, empleado_id: int, turno_id_nuevo: int) -> None:
        """Sub-3.2.B: ningún día del nuevo turno puede coincidir con un vigente.

        Itera las asignaciones vigentes del empleado, lee el ``dias_semana``
        de cada turno y rechaza si la intersección con el nuevo es no vacía.
        """
        turno_nuevo = self._turno_read.get_by_id(turno_id_nuevo)
        # _validar_turno_activo ya garantizó que existe.
        assert turno_nuevo is not None
        bitmask_nuevo = turno_nuevo.dias_semana
        for vigente in self._et_read.list_vigentes(empleado_id):
            turno_vigente = self._turno_read.get_by_id(vigente.turno_id)
            if turno_vigente is None:
                continue
            if turno_vigente.dias_semana & bitmask_nuevo:
                raise TurnoBitmaskSolapadoError(empleado_id, vigente.turno_id)

    def _validar_dni_unico(self, dni: str, excepto_id: Optional[int]) -> None:
        """Valida que el DNI no esté tomado por OTRO empleado."""
        existente = self._emp_read.get_by_dni(dni)
        if existente is not None and existente.id != excepto_id:
            raise DuplicateDNIError(dni)

    def _validar_zkteco_unico(self, zkteco_id: Optional[int], excepto_id: Optional[int]) -> None:
        """Valida que el ``zkteco_id`` no esté tomado por OTRO empleado.

        Si ``zkteco_id`` es ``None``, no hay nada que validar — múltiples
        empleados pueden tener ``None`` simultáneamente por semántica
        ``NULL != NULL`` de SQLite.
        """
        if zkteco_id is None:
            return
        existente = self._emp_read.get_by_zkteco_id(zkteco_id)
        if existente is not None and existente.id != excepto_id:
            raise DuplicateZktecoIdError(zkteco_id)
