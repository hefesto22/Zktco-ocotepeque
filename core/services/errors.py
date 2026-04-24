"""Excepciones de dominio de la capa de servicios.

Se definen aquí para que los controladores de UI puedan capturarlas
por tipo específico (regla del proyecto: "nunca capturar Exception
genérica").

Los mensajes asociados son en español y pensados para ser mostrados
al usuario final. Los mensajes de login son genéricos por seguridad
(nunca revelar si el usuario o la contraseña es lo incorrecto).
"""

from __future__ import annotations


class AuthError(Exception):
    """Clase base para errores de autenticación."""


class InvalidCredentialsError(AuthError):
    """Credenciales inválidas (usuario o contraseña).

    El mensaje es intencionalmente ambiguo: no distingue entre
    "usuario no existe" y "password incorrecta". Esto evita que un
    atacante pueda enumerar usuarios válidos probando logins.
    """

    def __init__(self, mensaje: str = "Usuario o contraseña incorrectos.") -> None:
        super().__init__(mensaje)


class AccountLockedError(AuthError):
    """Cuenta bloqueada por demasiados intentos fallidos.

    Attributes:
        locked_until: ISO-8601 UTC hasta cuando la cuenta permanece
            bloqueada. El caller puede usarlo para calcular cuánto
            falta y mostrarlo al usuario.
    """

    def __init__(self, locked_until: str) -> None:
        super().__init__(
            f"La cuenta está bloqueada temporalmente. "
            f"Vuelva a intentar después de {locked_until}."
        )
        self.locked_until = locked_until


class AccountInactiveError(AuthError):
    """El usuario existe y la password es correcta, pero ``is_active=False``."""

    def __init__(self) -> None:
        super().__init__("La cuenta está desactivada. Contacte al administrador.")


class PermissionDeniedError(Exception):
    """El usuario autenticado no tiene permiso para la acción solicitada.

    Attributes:
        permission: Código del permiso que se intentó verificar.
    """

    def __init__(self, permission: str) -> None:
        super().__init__(f"No tiene permisos para realizar esta acción ({permission}).")
        self.permission = permission


class NotAuthenticatedError(Exception):
    """Se intentó verificar un permiso sin una sesión activa."""

    def __init__(self) -> None:
        super().__init__("Debe iniciar sesión para continuar.")


# ── Errores del Setup Wizard ──────────────────────────────────────────────────


class SetupError(Exception):
    """Clase base para errores del Setup Wizard."""


class SetupAlreadyCompletedError(SetupError):
    """Se intentó ejecutar el wizard cuando ya existe un SUPERADMIN.

    El wizard queda deshabilitado de forma derivada: mientras
    ``count(SUPERADMIN) > 0``, cualquier intento de ``create_superadmin``
    lanza este error. No hay bandera persistida que un atacante pueda
    manipular — la única fuente de verdad es el conteo de usuarios.
    """

    def __init__(self) -> None:
        super().__init__(
            "El sistema ya tiene un SUPERADMIN configurado. "
            "El asistente de configuración ya no está disponible."
        )


class WeakPasswordError(SetupError):
    """La password no cumple la política mínima del proyecto.

    Attributes:
        reason: Descripción en español de qué regla no se cumplió.
            Es seguro mostrarla al usuario — el wizard es un flujo
            local, no expuesto por red, así que no aplican
            consideraciones de enumeración.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DuplicateUsernameError(SetupError):
    """El username ya existe en la tabla ``usuarios``.

    Attributes:
        username: El nombre que colisionó. Se expone porque el caller
            (CLI local) ya conoce el valor — lo acaba de tipear.
    """

    def __init__(self, username: str) -> None:
        super().__init__(f"El usuario '{username}' ya existe.")
        self.username = username


# ── Errores de validación genéricos (Fase 2) ──────────────────────────────────


class ValidationError(Exception):
    """Clase base para errores de validación de entradas en servicios Fase 2.

    Los servicios de dominio levantan subclases específicas antes de
    tocar el repositorio. Los mensajes son en español y seguros para
    mostrar al usuario final.
    """


class InvalidDNIError(ValidationError):
    """El DNI no cumple el formato hondureño ``XXXX-XXXX-XXXXX``."""

    def __init__(self, dni: str) -> None:
        super().__init__(
            f"El DNI '{dni}' no tiene el formato esperado (XXXX-XXXX-XXXXX, "
            "solo dígitos y guiones)."
        )
        self.dni = dni


class InvalidDateError(ValidationError):
    """Una fecha ISO (``YYYY-MM-DD``) no es parseable o no existe."""

    def __init__(self, valor: str, campo: str) -> None:
        super().__init__(
            f"La fecha '{valor}' en el campo '{campo}' no es válida. " "Use el formato YYYY-MM-DD."
        )
        self.valor = valor
        self.campo = campo


class InvalidTimeError(ValidationError):
    """Una hora 24h (``HH:MM``) no es parseable."""

    def __init__(self, valor: str, campo: str) -> None:
        super().__init__(
            f"La hora '{valor}' en el campo '{campo}' no es válida. "
            "Use el formato HH:MM (24 horas)."
        )
        self.valor = valor
        self.campo = campo


class InvalidBitmaskError(ValidationError):
    """El bitmask de días de la semana no está en el rango 0..127."""

    def __init__(self, valor: int) -> None:
        super().__init__(
            f"El bitmask de días '{valor}' está fuera del rango válido "
            "(0 a 127). Debe seleccionar al menos un día."
        )
        self.valor = valor


class InvalidMotivoBajaError(ValidationError):
    """El motivo de baja no está en el catálogo cerrado ``MotivoBaja``."""

    def __init__(self, valor: str) -> None:
        super().__init__(
            f"El motivo de baja '{valor}' no es válido. "
            "Debe ser uno de los motivos predefinidos."
        )
        self.valor = valor


class MissingRequiredFieldError(ValidationError):
    """Un campo obligatorio vino vacío o en ``None``."""

    def __init__(self, campo: str) -> None:
        super().__init__(f"El campo '{campo}' es obligatorio.")
        self.campo = campo


# ── Errores del catálogo (departamentos / cargos) ─────────────────────────────


class CatalogoError(Exception):
    """Clase base para errores de CatalogoService."""


class DuplicateNombreError(CatalogoError):
    """Ya existe una entrada con ese nombre en el catálogo."""

    def __init__(self, catalogo: str, nombre: str) -> None:
        super().__init__(f"Ya existe un '{catalogo}' con el nombre '{nombre}'.")
        self.catalogo = catalogo
        self.nombre = nombre


class CatalogoNotFoundError(CatalogoError):
    """No existe una entrada con ese id en el catálogo."""

    def __init__(self, catalogo: str, id_: int) -> None:
        super().__init__(f"No se encontró el '{catalogo}' con id {id_}.")
        self.catalogo = catalogo
        self.id_ = id_


class CatalogoInUseError(CatalogoError):
    """Se intentó archivar un catálogo que aún tiene empleados activos."""

    def __init__(self, catalogo: str, id_: int, empleados_activos: int) -> None:
        super().__init__(
            f"No se puede archivar el '{catalogo}' (id {id_}): "
            f"tiene {empleados_activos} empleado(s) activo(s) asignado(s)."
        )
        self.catalogo = catalogo
        self.id_ = id_
        self.empleados_activos = empleados_activos


# ── Errores de turnos ─────────────────────────────────────────────────────────


class TurnoError(Exception):
    """Clase base para errores de TurnoService."""


class DuplicateTurnoNombreError(TurnoError):
    """Ya existe un turno con ese nombre."""

    def __init__(self, nombre: str) -> None:
        super().__init__(f"Ya existe un turno con el nombre '{nombre}'.")
        self.nombre = nombre


class TurnoNotFoundError(TurnoError):
    """No existe un turno con ese id."""

    def __init__(self, turno_id: int) -> None:
        super().__init__(f"No se encontró el turno con id {turno_id}.")
        self.turno_id = turno_id


class TurnoInactiveError(TurnoError):
    """Se intentó asignar un turno que está archivado."""

    def __init__(self, turno_id: int) -> None:
        super().__init__(f"El turno con id {turno_id} está archivado y no puede asignarse.")
        self.turno_id = turno_id


class InvalidDescansoError(TurnoError):
    """Los minutos de descanso son mayores o iguales a la duración del turno."""

    def __init__(self, minutos_descanso: int, duracion_minutos: int) -> None:
        super().__init__(
            f"Los {minutos_descanso} minutos de descanso exceden la "
            f"duración del turno ({duracion_minutos} minutos)."
        )
        self.minutos_descanso = minutos_descanso
        self.duracion_minutos = duracion_minutos


# ── Errores de empleados ──────────────────────────────────────────────────────


class EmpleadoError(Exception):
    """Clase base para errores de EmpleadoService."""


class DuplicateDNIError(EmpleadoError):
    """Ya existe un empleado con ese DNI."""

    def __init__(self, dni: str) -> None:
        super().__init__(f"Ya existe un empleado con el DNI '{dni}'.")
        self.dni = dni


class DuplicateZktecoIdError(EmpleadoError):
    """Ya existe un empleado con ese ``zkteco_id``."""

    def __init__(self, zkteco_id: int) -> None:
        super().__init__(f"Ya existe un empleado con el ZKTeco ID {zkteco_id}.")
        self.zkteco_id = zkteco_id


class EmpleadoNotFoundError(EmpleadoError):
    """No existe un empleado con ese id."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(f"No se encontró el empleado con id {empleado_id}.")
        self.empleado_id = empleado_id


class EmpleadoAlreadyInactiveError(EmpleadoError):
    """Se intentó desactivar un empleado que ya está archivado."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(f"El empleado con id {empleado_id} ya está archivado.")
        self.empleado_id = empleado_id


class EmpleadoAlreadyActiveError(EmpleadoError):
    """Se intentó reactivar un empleado que ya está activo."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(f"El empleado con id {empleado_id} ya está activo.")
        self.empleado_id = empleado_id


class SinTurnoVigenteError(EmpleadoError):
    """Se intentó cambiar el turno de un empleado que no tiene vigente."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(
            f"El empleado con id {empleado_id} no tiene asignación de turno "
            "vigente. Use asignar_turno() en vez de cambiar_turno()."
        )
        self.empleado_id = empleado_id


class TurnoYaAsignadoError(EmpleadoError):
    """Se intentó asignar un turno cuando el empleado ya tiene uno vigente."""

    def __init__(self, empleado_id: int) -> None:
        super().__init__(
            f"El empleado con id {empleado_id} ya tiene un turno vigente. "
            "Use cambiar_turno() para reemplazarlo."
        )
        self.empleado_id = empleado_id


# ── Errores de sincronización (Fase 3) ────────────────────────────────────────


class SincronizacionError(Exception):
    """Clase base para errores de ``SincronizacionService``."""


class DispositivoNotFoundError(SincronizacionError):
    """No existe un dispositivo con ese id."""

    def __init__(self, dispositivo_id: int) -> None:
        super().__init__(f"No se encontró el dispositivo con id {dispositivo_id}.")
        self.dispositivo_id = dispositivo_id


class DispositivoInactiveError(SincronizacionError):
    """Se intentó sincronizar un dispositivo archivado."""

    def __init__(self, dispositivo_id: int) -> None:
        super().__init__(
            f"El dispositivo con id {dispositivo_id} está archivado y no "
            "puede sincronizarse. Reactívelo primero."
        )
        self.dispositivo_id = dispositivo_id


class InvalidRangoError(SincronizacionError):
    """El rango de sincronización es inválido (``desde > hasta``)."""

    def __init__(self, desde: str, hasta: str) -> None:
        super().__init__(
            f"El rango solicitado es inválido: desde={desde} es posterior a hasta={hasta}."
        )
        self.desde = desde
        self.hasta = hasta


# ── Errores de consolidación (Fase 3) ─────────────────────────────────────────


class ConsolidacionError(Exception):
    """Clase base para errores de ``ConsolidacionService``."""
