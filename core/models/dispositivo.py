"""Modelo de dominio: Dispositivo ZKTeco.

Representa un reloj checador físico (p.ej. sede principal, alcaldía, obras).
El diseño multi-dispositivo desde día 1 permite que el sistema escale sin
migraciones de datos cuando la municipalidad agregue relojes.

``Dispositivo`` NO es ``frozen`` — los flujos de edición (renombrar, cambiar
IP, archivar) lo mutan antes de persistir.

El puerto default 4370 es el estándar TCP de los ZKTeco (documentado en el
datasheet de pyzk). Se sobreescribe cuando el cliente usa otro puerto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional

PUERTO_ZKTECO_DEFAULT: Final[int] = 4370


@dataclass
class Dispositivo:
    """Reloj ZKTeco configurado en el sistema.

    Atributos:
        id: PK en la tabla ``dispositivos``. ``None`` si aún no fue persistido.
        nombre: Nombre legible único para el operador. Ejemplos:
            "Sede Principal", "Alcaldía", "Bodega Obras Públicas".
        ip: Dirección IPv4 del reloj en la LAN. Texto libre validado por el
            servicio con ``ipaddress.ip_address`` (Fase 3.2).
        puerto: Puerto TCP del reloj. Default ``4370`` (estándar ZKTeco).
            Rango válido 1..65535.
        is_active: ``False`` archiva el dispositivo — deja de aparecer en el
            combo de sincronización pero preserva los registros históricos
            vinculados en ``registros_raw`` y ``sincronizaciones``.
    """

    id: Optional[int]
    nombre: str
    ip: str
    puerto: int = PUERTO_ZKTECO_DEFAULT
    is_active: bool = True
