"""Implementación real de ``IZKTecoAdapter`` usando la librería ``pyzk``.

pyzk expone el módulo ``zk`` (``from zk import ZK``) con el cliente
TCP/IP para dispositivos ZKTeco. Este adapter lo envuelve y:

    1. Ejecuta CONNECT → GET_ATTENDANCE → DISCONNECT en try/finally para
       garantizar cleanup aunque el fetch lance.
    2. Traduce las excepciones de pyzk y del socket subyacente a la
       jerarquía propia en ``exceptions.py`` — los callers nunca ven
       ``zk.exception.*``.
    3. Filtra las marcadas recibidas al rango solicitado (pyzk descarga
       todo el buffer del reloj en un solo comando; el filtrado por
       fecha se hace en Python).
    4. Mapea ``Attendance.status`` → ``TipoMarcada`` vía
       ``status_mapping.map_status_to_tipo_marcada``.

El timeout se inyecta por constructor (D5: Opción B + inyección
explícita). El composition root en ``ui/app.py`` lo lee de settings y
lo pasa acá. El adapter no conoce la tabla settings — es puro wrapper.
"""

from __future__ import annotations

import logging
import socket
from datetime import date, datetime, time
from typing import Final, List

from zk import ZK
from zk.exception import ZKErrorConnection, ZKErrorResponse, ZKNetworkError

from core.models.dispositivo import Dispositivo
from core.models.registro_raw import RegistroRaw

from .exceptions import ZKConnectionError, ZKProtocolError, ZKTimeoutError
from .interface import IZKTecoAdapter
from .status_mapping import map_status_to_tipo_marcada

_DEFAULT_TIMEOUT_SECONDS: Final[int] = 10


class PyzkAdapter(IZKTecoAdapter):
    """Adapter real: habla con un dispositivo ZKTeco físico vía pyzk."""

    def __init__(self, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Inicializa el adapter.

        Args:
            timeout_seconds: Timeout de conexión y respuesta, en segundos.
                Debe ser ``> 0``. Se pasa tal cual al constructor de
                ``ZK`` de pyzk.

        Raises:
            ValueError: Si ``timeout_seconds`` no es positivo.
        """
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds debe ser > 0, recibido: {timeout_seconds}")
        self._timeout_seconds = timeout_seconds
        self._log = logging.getLogger(self.__class__.__name__)

    @property
    def timeout_seconds(self) -> int:
        """Timeout configurado (expuesto para tests y auditoría)."""
        return self._timeout_seconds

    def pull_attendance(
        self,
        dispositivo: Dispositivo,
        sincronizacion_id: int,
        desde: date,
        hasta: date,
    ) -> List[RegistroRaw]:
        """Descarga marcadas del rango [desde, hasta] (ambos inclusive).

        Ver contrato completo en ``IZKTecoAdapter.pull_attendance``.
        """
        self._validate_inputs(dispositivo, desde, hasta)
        assert dispositivo.id is not None  # validado arriba, para mypy

        # ``ommit_ping=True`` desactiva el ICMP ping interno que pyzk hace
        # antes del CONNECT. Ese check falla en redes con ICMP filtrado (firewalls
        # corporativos, AnyDesk redirigiendo, segmentos con ACL restrictiva) aunque
        # el TCP al puerto 4370 esté perfectamente accesible. Validado en campo
        # 2026-04-29 con K40 firmware Ver 6.60: sin la flag → timeout; con la flag
        # → connect inmediato. La verificación de "device alive" la hace nuestra
        # app en SincronizacionView (botón Sincronizar) capturando ZKConnectionError
        # del propio CONNECT TCP — más fiable que el ping y sin falsos negativos.
        client = ZK(
            dispositivo.ip,
            port=dispositivo.puerto,
            timeout=self._timeout_seconds,
            ommit_ping=True,
        )
        conn = self._connect(client, dispositivo)
        try:
            raw_attendances = self._fetch(conn, dispositivo)
        finally:
            self._disconnect_quietly(conn)

        return self._to_registros_raw(
            raw_attendances,
            dispositivo_id=dispositivo.id,
            sincronizacion_id=sincronizacion_id,
            desde=desde,
            hasta=hasta,
        )

    # ── Helpers privados ──────────────────────────────────────────────

    @staticmethod
    def _validate_inputs(dispositivo: Dispositivo, desde: date, hasta: date) -> None:
        """Valida precondiciones antes de tocar la red."""
        if dispositivo.id is None:
            raise ValueError("Dispositivo debe estar persistido (id requerido)")
        if desde > hasta:
            raise ValueError(f"Rango invertido: desde={desde} > hasta={hasta}")

    def _connect(self, client: ZK, dispositivo: Dispositivo) -> object:
        """Establece la conexión traduciendo excepciones de pyzk."""
        try:
            return client.connect()
        except socket.timeout as exc:
            raise ZKTimeoutError(
                f"Timeout al conectar con {dispositivo.nombre} "
                f"({dispositivo.ip}:{dispositivo.puerto})."
            ) from exc
        except (ZKNetworkError, ZKErrorConnection, OSError) as exc:
            raise ZKConnectionError(
                f"No se pudo conectar al dispositivo {dispositivo.nombre} "
                f"({dispositivo.ip}:{dispositivo.puerto}). Verifique que esté "
                f"encendido y accesible en la red."
            ) from exc
        except ZKErrorResponse as exc:
            raise ZKProtocolError(
                f"Respuesta inesperada del dispositivo {dispositivo.nombre} "
                f"al conectar — posible incompatibilidad de firmware."
            ) from exc

    def _fetch(self, conn: object, dispositivo: Dispositivo) -> list[object]:
        """Descarga el buffer completo de marcadas del reloj."""
        try:
            result = conn.get_attendance()  # type: ignore[attr-defined]
            if not result:
                return []
            items: list[object] = []
            for att in result:
                items.append(att)
            return items
        except socket.timeout as exc:
            raise ZKTimeoutError(f"Timeout al descargar marcadas de {dispositivo.nombre}.") from exc
        except ZKErrorResponse as exc:
            raise ZKProtocolError(
                f"Respuesta corrupta de {dispositivo.nombre} al leer marcadas."
            ) from exc
        except (ZKNetworkError, ZKErrorConnection, OSError) as exc:
            raise ZKConnectionError(
                f"Conexión interrumpida con {dispositivo.nombre} durante "
                f"la descarga de marcadas."
            ) from exc

    def _disconnect_quietly(self, conn: object) -> None:
        """Cierra la conexión ignorando errores de cleanup.

        Si el disconnect falla (dispositivo ya se cayó, socket roto),
        loggeamos pero no lanzamos — el fetch anterior ya pudo haber
        sido exitoso y no queremos enmascarar ese resultado.
        """
        try:
            conn.disconnect()  # type: ignore[attr-defined]
        except (ZKNetworkError, ZKErrorConnection, ZKErrorResponse, OSError) as exc:
            self._log.warning("Fallo al desconectar del ZKTeco (ignorado): %s", exc)

    def _to_registros_raw(
        self,
        raw_attendances: list[object],
        *,
        dispositivo_id: int,
        sincronizacion_id: int,
        desde: date,
        hasta: date,
    ) -> List[RegistroRaw]:
        """Filtra por rango y mapea a la lista final de ``RegistroRaw``."""
        desde_dt = datetime.combine(desde, time.min)
        hasta_dt = datetime.combine(hasta, time.max)
        resultado: List[RegistroRaw] = []
        for att in raw_attendances:
            ts: datetime = att.timestamp  # type: ignore[attr-defined]
            if ts < desde_dt or ts > hasta_dt:
                continue
            tipo = map_status_to_tipo_marcada(int(att.status))  # type: ignore[attr-defined]
            resultado.append(
                RegistroRaw(
                    id=None,
                    dispositivo_id=dispositivo_id,
                    sincronizacion_id=sincronizacion_id,
                    zkteco_user_id=int(att.user_id),  # type: ignore[attr-defined]
                    timestamp=ts.isoformat(timespec="seconds"),
                    tipo_marcada=tipo.value,
                )
            )
        return resultado
