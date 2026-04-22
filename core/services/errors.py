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
