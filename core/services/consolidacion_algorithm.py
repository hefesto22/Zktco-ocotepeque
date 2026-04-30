"""Funciones puras del algoritmo de consolidación raw → asistencia.

Este módulo contiene la lógica determinística que decide, para un
empleado y un día:

    1. Qué turno le aplicaba (consultando su historial de asignaciones).
    2. Qué marcadas pertenecen a ese día (ventana temporal correcta,
       incluyendo turnos que cruzan medianoche).
    3. Cuáles son la entrada y la salida (algoritmo "ventana de entrada"
       — Sub-2.7d, decisión 6).
    4. Qué estado final de asistencia corresponde (aplicando tolerancias
       de entrada y salida configuradas por turno).

Algoritmo "ventana de entrada" (Sub-2.7d):
    El K40 (firmware estándar ZKTeco) NO distingue entrada vs salida al
    pulsar — todas las marcadas suelen llegar como CHECK_OUT o UNKNOWN.
    En vez de confiar en ese campo, deducimos por HORA DEL DÍA:

        cutoff_entrada = hora_oficial_entrada + ``VENTANA_ENTRADA_MIN``
        - Marcadas con timestamp <= cutoff: candidatas a ENTRADA.
          La primera (cronológica) es la entrada. Las demás se ignoran
          como ruido (el operador presionó la huella varias veces al
          llegar — todas valen como una sola entrada).
        - Marcadas con timestamp > cutoff: candidatas a SALIDA.
          La última es la salida. Igual: si presionó varias veces al
          irse, todas se pliegan en la última.

    El campo ``tipo_marcada`` del raw queda como auditoría histórica
    pero NO se usa para el pareo. Esto generaliza al hecho que en la
    mayoría de relojes ZKTeco low-end el operador no presiona la tecla
    de entrada/salida explícita — solo apoya el dedo.

Se extrae a un módulo aparte — separado de ``ConsolidacionService`` —
porque son funciones puras sin side effects. Esto permite testearlas
exhaustivamente sin montar BD ni fakes.

Convenciones:
    - Todas las horas en formato ``"HH:MM"`` 24h (entrada/salida del turno).
    - Todos los timestamps en formato ``"YYYY-MM-DDTHH:MM:SS"`` (raw).
    - Todas las fechas en formato ISO ``"YYYY-MM-DD"``.
    - Turno que cruza medianoche: la fecha de la asistencia es el día de
      ENTRADA aunque la salida caiga al día siguiente (convención del
      modelo ``Asistencia``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from core.models.asistencia import EstadoAsistencia
from core.models.empleado_turno import EmpleadoTurno
from core.models.registro_raw import RegistroRaw
from core.models.turno import Turno, dia_aplica

# Minutos después de la hora oficial de entrada que aún cuentan como
# "candidata a entrada". Sub-2.7d: el operador puede apoyar la huella
# en cualquier momento dentro de esta franja al llegar; cualquier
# marcada posterior al cutoff cuenta como salida.
#
# 60 cubre con holgura los turnos típicos de la municipalidad
# (08:00-17:00 → cutoff 09:00). Si una organización quisiera más
# tolerancia, este valor se podría mover a config.py o a un campo del
# turno; por ahora hardcodeado para mantener simplicidad.
VENTANA_ENTRADA_MINUTOS_DEFAULT: int = 60

# ── Formatos ──────────────────────────────────────────────────────────────────

_FMT_FECHA: str = "%Y-%m-%d"
_FMT_TIMESTAMP: str = "%Y-%m-%dT%H:%M:%S"
_FMT_HORA: str = "%H:%M"


# ── Resultado del pareo de marcadas ───────────────────────────────────────────


@dataclass(frozen=True)
class MarcadasDelDia:
    """Entrada y salida inferidas de un grupo de marcadas para un día.

    Attributes:
        entrada: Timestamp de entrada efectiva, o ``None`` si no hay marcada
            clasificable como entrada.
        salida: Timestamp de salida efectiva, o ``None`` si no hay marcada
            clasificable como salida.
    """

    entrada: Optional[datetime]
    salida: Optional[datetime]


# ── Resolución de turno vigente en una fecha histórica ────────────────────────


def resolver_turno_en_fecha(
    historial: List[EmpleadoTurno],
    fecha_iso: str,
) -> Optional[int]:
    """Devuelve el ``turno_id`` vigente para el empleado en la fecha dada.

    Recorre el historial de asignaciones (``empleado_turnos``) buscando
    una fila cuyo rango ``[fecha_inicio, fecha_fin]`` contenga ``fecha_iso``.
    Las asignaciones vigentes (``fecha_fin IS NULL``) se tratan como si
    su fin fuera infinito.

    Args:
        historial: Todas las filas de ``empleado_turnos`` del empleado
            (cualquier orden; la función lo itera completo). Vacío o
            ``None`` no aplica — pasar lista vacía si el empleado no tiene
            historial.
        fecha_iso: Fecha a consultar en formato ``"YYYY-MM-DD"``.

    Returns:
        El ``turno_id`` de la asignación que cubre esa fecha, o ``None``
        si el empleado no tenía turno asignado ese día (día previo a su
        primera asignación, o en un hueco entre asignaciones cerradas).

    Raises:
        ValueError: Si ``fecha_iso`` no es parseable.
    """
    fecha_consulta = _parse_fecha(fecha_iso, campo="fecha_iso")
    for asignacion in historial:
        if not _asignacion_cubre_fecha(asignacion, fecha_consulta):
            continue
        return asignacion.turno_id
    return None


def _asignacion_cubre_fecha(asignacion: EmpleadoTurno, fecha: date) -> bool:
    """Predicado puro: ¿la asignación cubre la fecha dada?

    Una asignación cubre la fecha si:
        fecha_inicio <= fecha  AND
        (fecha_fin is None  OR  fecha_fin >= fecha)
    """
    inicio = _parse_fecha(asignacion.fecha_inicio, campo="fecha_inicio")
    if fecha < inicio:
        return False
    if asignacion.fecha_fin is None:
        return True
    fin = _parse_fecha(asignacion.fecha_fin, campo="fecha_fin")
    return fecha <= fin


# ── Ventana temporal para un día de turno ─────────────────────────────────────


def ventana_del_dia(turno: Turno, fecha_iso: str) -> Tuple[datetime, datetime]:
    """Calcula la ventana ``[inicio, fin]`` en la que caen las marcadas del día.

    La ventana se dimensiona así:

        - Turno normal (``cruza_medianoche=False``):
              [fecha T hora_entrada - tol_entrada,
               fecha T hora_salida  + tol_salida]

        - Turno nocturno (``cruza_medianoche=True``):
              [fecha      T hora_entrada - tol_entrada,
               fecha+1día T hora_salida  + tol_salida]

    Las tolerancias amplían la ventana para capturar marcadas cercanas
    al inicio/fin que aún deberían contarse como del día (llegadas con
    gracia, salidas muy puntuales).

    Args:
        turno: Turno vigente del empleado ese día.
        fecha_iso: Día de ENTRADA del turno, formato ``"YYYY-MM-DD"``.

    Returns:
        Tupla ``(inicio_ventana, fin_ventana)`` como ``datetime`` naive.

    Raises:
        ValueError: Si alguna de las horas o la fecha son inválidas.
    """
    dia = _parse_fecha(fecha_iso, campo="fecha_iso")
    hora_entrada = _parse_hora(turno.hora_entrada, campo="hora_entrada")
    hora_salida = _parse_hora(turno.hora_salida, campo="hora_salida")

    inicio_ventana = datetime.combine(dia, hora_entrada) - timedelta(
        minutes=turno.minutos_tolerancia_entrada
    )

    dia_salida = dia + timedelta(days=1) if turno.cruza_medianoche else dia
    fin_ventana = datetime.combine(dia_salida, hora_salida) + timedelta(
        minutes=turno.minutos_tolerancia_salida
    )
    return inicio_ventana, fin_ventana


# ── Cutoff de entrada (Sub-2.7d) ──────────────────────────────────────────────


def cutoff_entrada_del_turno(
    turno: Turno,
    fecha_iso: str,
    ventana_minutos: int = VENTANA_ENTRADA_MINUTOS_DEFAULT,
) -> datetime:
    """Calcula el límite temporal entre "candidato a entrada" y "candidato a salida".

    El cutoff es la hora oficial de entrada del turno desplazada
    ``ventana_minutos`` hacia adelante. Cualquier marcada con timestamp
    <= cutoff cuenta como entrada (la primera gana); el resto cuenta
    como salida (la última gana).

    Args:
        turno: Turno vigente del empleado ese día.
        fecha_iso: Día de ENTRADA del turno, ``"YYYY-MM-DD"``.
        ventana_minutos: Cuántos minutos después de la entrada oficial
            siguen contando como zona de entrada. Default 60.

    Returns:
        ``datetime`` naive marcando el cutoff.
    """
    dia = _parse_fecha(fecha_iso, campo="fecha_iso")
    hora_entrada = _parse_hora(turno.hora_entrada, campo="hora_entrada")
    return datetime.combine(dia, hora_entrada) + timedelta(minutes=ventana_minutos)


# ── Pareo de marcadas (algoritmo "ventana de entrada", Sub-2.7d) ─────────────


def parear_marcadas(
    registros: List[RegistroRaw],
    inicio_ventana: datetime,
    fin_ventana: datetime,
    cutoff_entrada: datetime,
) -> MarcadasDelDia:
    """Determina la entrada y la salida efectivas para un grupo de marcadas.

    Algoritmo "ventana de entrada" (Sub-2.7d, decisión 6):

        1. Filtra marcadas dentro de ``[inicio_ventana, fin_ventana]``.
        2. Las que cayeron <= ``cutoff_entrada`` son candidatas a ENTRADA.
           La PRIMERA (cronológica) gana — las posteriores se ignoran
           como ruido (el operador apoyó la huella varias veces).
        3. Las que cayeron > ``cutoff_entrada`` son candidatas a SALIDA.
           La ÚLTIMA gana — el resto son intermedias (ej. si abrió la
           huella varias veces antes de irse).
        4. NO se inspecciona ``tipo_marcada``: el K40 estándar reporta
           todo como CHECK_OUT/UNKNOWN, así que no es confiable.

    Casos borde:
        - Sin marcadas en la ventana → ``(None, None)`` → AUSENTE.
        - Solo marcadas <= cutoff → entrada poblada, salida ``None`` →
          INCOMPLETO (entró pero no marcó salida).
        - Solo marcadas > cutoff → entrada ``None``, salida poblada →
          INCOMPLETO (no marcó entrada o marcó muy tarde).

    Args:
        registros: Marcadas candidatas (cualquier orden; se ordena
            internamente). Pueden venir de cualquier dispositivo — se
            asume que el caller ya filtró por empleado.
        inicio_ventana: Límite inferior inclusive (marcadas anteriores
            se descartan).
        fin_ventana: Límite superior inclusive (marcadas posteriores se
            descartan).
        cutoff_entrada: Frontera temporal entrada vs salida. Típicamente
            ``cutoff_entrada_del_turno(turno, fecha)``.

    Returns:
        ``MarcadasDelDia(entrada, salida)`` con ``datetime`` naive o
        ``None`` en cada campo.
    """
    en_ventana = _filtrar_y_ordenar(registros, inicio_ventana, fin_ventana)
    if not en_ventana:
        return MarcadasDelDia(entrada=None, salida=None)

    entrada: Optional[datetime] = None
    salida: Optional[datetime] = None
    for ts in (_parse_timestamp(r.timestamp) for r in en_ventana):
        if ts <= cutoff_entrada:
            # Primera marcada en zona de entrada gana; las demás se ignoran.
            if entrada is None:
                entrada = ts
        else:
            # En zona de salida la última gana — sobreescribimos en cada paso.
            salida = ts
    return MarcadasDelDia(entrada=entrada, salida=salida)


def _filtrar_y_ordenar(
    registros: List[RegistroRaw],
    inicio: datetime,
    fin: datetime,
) -> List[RegistroRaw]:
    """Descarta marcadas fuera de la ventana y ordena por timestamp ASC."""
    dentro: List[Tuple[datetime, RegistroRaw]] = []
    for r in registros:
        ts = _parse_timestamp(r.timestamp)
        if inicio <= ts <= fin:
            dentro.append((ts, r))
    dentro.sort(key=lambda par: par[0])
    return [r for _, r in dentro]


# ── Derivación del estado de asistencia ───────────────────────────────────────


@dataclass(frozen=True)
class EstadoDerivado:
    """Output de ``derivar_estado``: estado + minutos + horas formateadas.

    Los campos quedan listos para construir un ``Asistencia`` sin
    transformaciones adicionales en el servicio.

    Attributes:
        estado: Uno de ``EstadoAsistencia.*.value``.
        hora_entrada_real: ``"HH:MM:SS"`` o ``None``.
        hora_salida_real: ``"HH:MM:SS"`` o ``None``.
        minutos_tarde: 0 cuando no aplica.
        minutos_salida_temprana: 0 cuando no aplica.
    """

    estado: str
    hora_entrada_real: Optional[str]
    hora_salida_real: Optional[str]
    minutos_tarde: int
    minutos_salida_temprana: int


def derivar_estado(marcadas: MarcadasDelDia, turno: Turno, fecha_iso: str) -> EstadoDerivado:
    """Calcula estado + minutos de desviación a partir de marcadas y turno.

    Reglas (aplicadas en orden):

        1. Si no hay entrada y no hay salida → AUSENTE.
        2. Si falta una de las dos → INCOMPLETO (con la que sí está).
        3. Si hay ambas:
              - Si entrada ≤ hora_oficial_entrada + tolerancia_entrada y
                salida ≥ hora_oficial_salida - tolerancia_salida → PRESENTE.
              - tarde = True si entrada > hora_oficial_entrada + tolerancia_entrada.
              - salida_temprana = True si salida < hora_oficial_salida - tolerancia_salida.
              - Combinar flags para TARDE, SALIDA_TEMPRANA o TARDE_Y_SALIDA_TEMPRANA.

    Los minutos reportados son los de desviación ESTRICTA (sin tolerancia):

        minutos_tarde = max(0, entrada - hora_oficial_entrada) en minutos.
        minutos_salida_temprana = max(0, hora_oficial_salida - salida).

    Se reportan incluso cuando están dentro de tolerancia (útil para
    reportes ejecutivos); la tolerancia solo afecta el valor de ``estado``.

    Args:
        marcadas: Entrada y salida del día (puede tener ``None`` en uno
            o ambos campos).
        turno: Turno vigente ese día.
        fecha_iso: Día de ENTRADA del turno, ``"YYYY-MM-DD"``. Se usa
            para decidir a qué fecha se refiere cada hora oficial.

    Returns:
        ``EstadoDerivado`` con el estado final + campos derivados.

    Raises:
        ValueError: Si las horas del turno son inválidas.
    """
    entrada = marcadas.entrada
    salida = marcadas.salida

    if entrada is None and salida is None:
        return EstadoDerivado(
            estado=EstadoAsistencia.AUSENTE.value,
            hora_entrada_real=None,
            hora_salida_real=None,
            minutos_tarde=0,
            minutos_salida_temprana=0,
        )

    if entrada is None or salida is None:
        return EstadoDerivado(
            estado=EstadoAsistencia.INCOMPLETO.value,
            hora_entrada_real=_fmt_hora_real(entrada),
            hora_salida_real=_fmt_hora_real(salida),
            minutos_tarde=0,
            minutos_salida_temprana=0,
        )

    entrada_oficial, salida_oficial = _horas_oficiales(turno, fecha_iso)
    minutos_tarde = _minutos_positivos(entrada - entrada_oficial)
    minutos_temprana = _minutos_positivos(salida_oficial - salida)
    es_tarde = minutos_tarde > turno.minutos_tolerancia_entrada
    es_temprana = minutos_temprana > turno.minutos_tolerancia_salida

    estado = _combinar_flags(es_tarde, es_temprana)
    return EstadoDerivado(
        estado=estado,
        hora_entrada_real=_fmt_hora_real(entrada),
        hora_salida_real=_fmt_hora_real(salida),
        minutos_tarde=minutos_tarde,
        minutos_salida_temprana=minutos_temprana,
    )


def _horas_oficiales(turno: Turno, fecha_iso: str) -> Tuple[datetime, datetime]:
    """Construye los datetimes oficiales de entrada y salida del turno ese día."""
    dia = _parse_fecha(fecha_iso, campo="fecha_iso")
    entrada_time = _parse_hora(turno.hora_entrada, campo="hora_entrada")
    salida_time = _parse_hora(turno.hora_salida, campo="hora_salida")
    entrada_oficial = datetime.combine(dia, entrada_time)
    dia_salida = dia + timedelta(days=1) if turno.cruza_medianoche else dia
    salida_oficial = datetime.combine(dia_salida, salida_time)
    return entrada_oficial, salida_oficial


def _combinar_flags(es_tarde: bool, es_temprana: bool) -> str:
    """Devuelve el valor de ``EstadoAsistencia`` según los flags."""
    if es_tarde and es_temprana:
        return EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value
    if es_tarde:
        return EstadoAsistencia.TARDE.value
    if es_temprana:
        return EstadoAsistencia.SALIDA_TEMPRANA.value
    return EstadoAsistencia.PRESENTE.value


def _minutos_positivos(delta: timedelta) -> int:
    """Devuelve los minutos del delta, truncado a 0 si es negativo.

    Usamos ``int(delta.total_seconds() // 60)`` para que un delta de
    89 segundos cuente como 1 minuto (no 0) — más estricto en frontera.
    """
    if delta.total_seconds() <= 0:
        return 0
    return int(delta.total_seconds() // 60)


def _fmt_hora_real(ts: Optional[datetime]) -> Optional[str]:
    """Formatea ``datetime`` a ``"HH:MM:SS"`` o devuelve ``None``."""
    if ts is None:
        return None
    return ts.strftime("%H:%M:%S")


# ── Cálculo del estado cuando no corresponde trabajar ─────────────────────────


def estado_no_trabaja(turno: Optional[Turno], fecha_iso: str, es_feriado: bool) -> Optional[str]:
    """Devuelve el estado si el empleado NO debía trabajar ese día, o ``None``.

    Se aplica en este orden:

        - Si ``es_feriado`` → ``FERIADO``. Gana sobre SIN_TURNO porque la
          intención del feriado es más fuerte que el calendario semanal
          del turno.
        - Si ``turno is None`` (no hay asignación vigente) → ``SIN_TURNO``.
        - Si el turno existe pero el día no aplica (bitmask
          ``dias_semana``) → ``SIN_TURNO``.
        - Si el turno aplica → ``None`` (el caller continúa con el flujo
          normal de marcadas).

    Args:
        turno: Turno vigente del empleado, o ``None``.
        fecha_iso: Día de consulta ``"YYYY-MM-DD"``.
        es_feriado: True si la fecha está en la tabla ``feriados``.

    Returns:
        El estado correspondiente o ``None`` si debe procesarse normalmente.
    """
    if es_feriado:
        return EstadoAsistencia.FERIADO.value
    if turno is None:
        return EstadoAsistencia.SIN_TURNO.value
    fecha = _parse_fecha(fecha_iso, campo="fecha_iso")
    if not dia_aplica(turno.dias_semana, fecha.weekday()):
        return EstadoAsistencia.SIN_TURNO.value
    return None


# ── Helpers de parsing ────────────────────────────────────────────────────────


def _parse_fecha(valor: str, campo: str) -> date:
    """Parsea ``"YYYY-MM-DD"`` a ``date`` o lanza ``ValueError`` descriptivo."""
    try:
        return datetime.strptime(valor, _FMT_FECHA).date()
    except ValueError as exc:
        raise ValueError(f"Fecha inválida en '{campo}': '{valor}' (esperado YYYY-MM-DD)") from exc


def _parse_hora(valor: str, campo: str) -> time:
    """Parsea ``"HH:MM"`` 24h a ``time`` o lanza ``ValueError`` descriptivo."""
    try:
        return datetime.strptime(valor, _FMT_HORA).time()
    except ValueError as exc:
        raise ValueError(f"Hora inválida en '{campo}': '{valor}' (esperado HH:MM)") from exc


def _parse_timestamp(valor: str) -> datetime:
    """Parsea timestamp raw ``"YYYY-MM-DDTHH:MM:SS"`` a ``datetime`` naive."""
    try:
        return datetime.strptime(valor, _FMT_TIMESTAMP)
    except ValueError as exc:
        raise ValueError(f"Timestamp inválido: '{valor}' (esperado YYYY-MM-DDTHH:MM:SS)") from exc
