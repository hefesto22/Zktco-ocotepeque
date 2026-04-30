"""Tests del ``core.services.consolidacion_algorithm``.

Funciones puras sin BD ni fakes. Cubren:

    - ``resolver_turno_en_fecha``: resolución por rango histórico + huecos.
    - ``ventana_del_dia``: tolerancias + turno normal + turno nocturno.
    - ``parear_marcadas``: algoritmo híbrido (IN/OUT explícito + fallback
      UNKNOWN), ventana temporal, marcadas duplicadas.
    - ``derivar_estado``: AUSENTE, INCOMPLETO, PRESENTE, TARDE,
      SALIDA_TEMPRANA, TARDE_Y_SALIDA_TEMPRANA — con y sin tolerancias.
    - ``estado_no_trabaja``: prioridad FERIADO > SIN_TURNO, bitmask de días.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

import pytest

from core.models.asistencia import EstadoAsistencia
from core.models.empleado_turno import EmpleadoTurno
from core.models.registro_raw import RegistroRaw, TipoMarcada
from core.models.turno import (
    DIAS_FIN_DE_SEMANA,
    DIAS_LABORALES,
    DIAS_TODA_LA_SEMANA,
    LUNES,
    Turno,
)
from core.services.consolidacion_algorithm import (
    VENTANA_ENTRADA_MINUTOS_DEFAULT,
    MarcadasDelDia,
    cutoff_entrada_del_turno,
    derivar_estado,
    estado_no_trabaja,
    parear_marcadas,
    resolver_turno_en_fecha,
    ventana_del_dia,
)

# ── Factories ────────────────────────────────────────────────────────────────


def _turno(
    id_: int = 1,
    hora_entrada: str = "08:00",
    hora_salida: str = "17:00",
    dias: int = DIAS_LABORALES,
    tol_entrada: int = 10,
    tol_salida: int = 0,
    cruza_medianoche: bool = False,
) -> Turno:
    return Turno(
        id=id_,
        nombre=f"Turno {id_}",
        hora_entrada=hora_entrada,
        hora_salida=hora_salida,
        dias_semana=dias,
        cruza_medianoche=cruza_medianoche,
        minutos_tolerancia_entrada=tol_entrada,
        minutos_tolerancia_salida=tol_salida,
    )


def _asignacion(
    id_: int,
    turno_id: int,
    fecha_inicio: str,
    fecha_fin: Optional[str] = None,
) -> EmpleadoTurno:
    return EmpleadoTurno(
        id=id_,
        empleado_id=100,
        turno_id=turno_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )


def _raw(
    timestamp: str,
    tipo: str = TipoMarcada.UNKNOWN.value,
    zkteco_user_id: int = 42,
) -> RegistroRaw:
    return RegistroRaw(
        id=None,
        dispositivo_id=1,
        sincronizacion_id=1,
        zkteco_user_id=zkteco_user_id,
        timestamp=timestamp,
        tipo_marcada=tipo,
    )


# ── resolver_turno_en_fecha ──────────────────────────────────────────────────


def test_resolver_turno_vigente_actual() -> None:
    historial: List[EmpleadoTurno] = [_asignacion(1, turno_id=10, fecha_inicio="2026-01-01")]
    assert resolver_turno_en_fecha(historial, "2026-04-15") == 10


def test_resolver_turno_cerrado_cubre_fecha() -> None:
    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01", fecha_fin="2026-03-31"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-04-01"),
    ]
    assert resolver_turno_en_fecha(historial, "2026-02-15") == 10
    assert resolver_turno_en_fecha(historial, "2026-04-15") == 20


def test_resolver_turno_en_borde_fecha_fin_inclusive() -> None:
    """``fecha_fin`` es inclusive — el último día aún pertenece a esa asignación."""
    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01", fecha_fin="2026-03-31"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-04-01"),
    ]
    assert resolver_turno_en_fecha(historial, "2026-03-31") == 10
    assert resolver_turno_en_fecha(historial, "2026-04-01") == 20


def test_resolver_turno_antes_de_primera_asignacion_devuelve_none() -> None:
    historial = [_asignacion(1, turno_id=10, fecha_inicio="2026-02-01")]
    assert resolver_turno_en_fecha(historial, "2026-01-15") is None


def test_resolver_turno_hueco_entre_asignaciones_cerradas_devuelve_none() -> None:
    historial = [
        _asignacion(1, turno_id=10, fecha_inicio="2026-01-01", fecha_fin="2026-01-31"),
        _asignacion(2, turno_id=20, fecha_inicio="2026-03-01"),
    ]
    assert resolver_turno_en_fecha(historial, "2026-02-15") is None


def test_resolver_turno_sin_historial_devuelve_none() -> None:
    assert resolver_turno_en_fecha([], "2026-04-15") is None


def test_resolver_turno_fecha_invalida_lanza_value_error() -> None:
    with pytest.raises(ValueError, match="Fecha inválida"):
        resolver_turno_en_fecha([], "no-fecha")


# ── ventana_del_dia ──────────────────────────────────────────────────────────


def test_ventana_turno_normal_respeta_tolerancias() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert inicio == datetime(2026, 4, 15, 7, 50)
    assert fin == datetime(2026, 4, 15, 17, 5)


def test_ventana_turno_nocturno_cruza_medianoche() -> None:
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=15,
        tol_salida=10,
    )
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert inicio == datetime(2026, 4, 15, 21, 45)
    # Fin cae al día siguiente.
    assert fin == datetime(2026, 4, 16, 6, 10)


def test_ventana_con_tolerancias_cero() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=0, tol_salida=0)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert inicio == datetime(2026, 4, 15, 8, 0)
    assert fin == datetime(2026, 4, 15, 17, 0)


# ── cutoff_entrada_del_turno (Sub-2.7d) ──────────────────────────────────────


def test_cutoff_default_es_60_min_despues_de_entrada() -> None:
    """Turno 08:00 → cutoff default = 09:00."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15")
    assert cutoff == datetime(2026, 4, 15, 9, 0)


def test_cutoff_acepta_ventana_personalizada() -> None:
    """Pasando ventana_minutos override se respeta."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15", ventana_minutos=90)
    assert cutoff == datetime(2026, 4, 15, 9, 30)


def test_cutoff_turno_de_tarde_funciona_igual() -> None:
    """No depende del horario absoluto; solo de hora_entrada del turno."""
    turno = _turno(hora_entrada="14:00", hora_salida="22:00")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15")
    assert cutoff == datetime(2026, 4, 15, 15, 0)


def test_cutoff_constante_default_es_60() -> None:
    assert VENTANA_ENTRADA_MINUTOS_DEFAULT == 60


# ── parear_marcadas (algoritmo "ventana de entrada", Sub-2.7d) ───────────────


def _cutoff_default(fecha: str = "2026-04-15") -> datetime:
    """Helper: cutoff de un turno 08:00 en la fecha dada (= 09:00)."""
    return cutoff_entrada_del_turno(_turno(hora_entrada="08:00"), fecha)


def test_parear_primera_antes_de_cutoff_es_entrada_ultima_despues_es_salida() -> None:
    """Caso típico: una marcada antes del cutoff y una después."""
    raws = [
        _raw("2026-04-15T08:00:00"),
        _raw("2026-04-15T17:00:00"),
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_multiples_antes_de_cutoff_solo_la_primera_es_entrada() -> None:
    """Si el operador apoya la huella varias veces al llegar, solo la
    PRIMERA marcada cuenta como entrada — el resto se ignora como ruido."""
    raws = [
        _raw("2026-04-15T08:00:00"),  # entrada — primera
        _raw("2026-04-15T08:05:00"),  # ruido — ignorada
        _raw("2026-04-15T08:30:00"),  # ruido — ignorada
        _raw("2026-04-15T17:00:00"),  # salida
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_multiples_despues_de_cutoff_solo_la_ultima_es_salida() -> None:
    """Si presiona la huella varias veces al irse, solo la ÚLTIMA es la salida."""
    raws = [
        _raw("2026-04-15T08:00:00"),  # entrada
        _raw("2026-04-15T16:30:00"),  # ruido
        _raw("2026-04-15T16:55:00"),  # ruido
        _raw("2026-04-15T17:00:00"),  # salida — última
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_solo_marcadas_antes_de_cutoff_salida_none() -> None:
    """Empleado marcó entrada pero no marcó salida → INCOMPLETO con
    entrada poblada."""
    raws = [
        _raw("2026-04-15T08:00:00"),
        _raw("2026-04-15T08:30:00"),
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida is None


def test_parear_solo_marcadas_despues_de_cutoff_entrada_none() -> None:
    """Solo hay marcadas después del cutoff: el empleado no marcó entrada
    o llegó muy tarde — entrada queda None, salida = última."""
    raws = [
        _raw("2026-04-15T13:00:00"),
        _raw("2026-04-15T17:00:00"),
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada is None
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_marcada_justo_en_cutoff_cuenta_como_entrada() -> None:
    """La marcada exactamente en el cutoff (ts == cutoff) cuenta como
    entrada (la inclusión es ``<=``)."""
    raws = [_raw("2026-04-15T09:00:00")]  # exactamente en cutoff 09:00
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 9, 0)
    assert marcadas.salida is None


def test_parear_descarta_marcadas_fuera_de_ventana() -> None:
    """Las marcadas fuera de [inicio, fin] se descartan antes de aplicar
    la regla de cutoff."""
    raws = [
        _raw("2026-04-14T23:00:00"),  # día anterior — fuera
        _raw("2026-04-15T08:00:00"),  # entrada válida
        _raw("2026-04-15T17:00:00"),  # salida válida
        _raw("2026-04-15T20:00:00"),  # después de fin — fuera
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_sin_marcadas_en_ventana_retorna_ambos_none() -> None:
    raws = [_raw("2026-04-14T23:00:00"), _raw("2026-04-16T01:00:00")]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada is None
    assert marcadas.salida is None


def test_parear_ignora_tipo_marcada() -> None:
    """Sub-2.7d: el algoritmo NO depende del tipo_marcada del K40.
    Una marcada CHECK_OUT antes del cutoff se trata como entrada igual,
    porque la HORA del día es la fuente de verdad."""
    raws = [
        _raw(
            "2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_OUT.value
        ),  # tipo "salida" pero antes del cutoff
        _raw(
            "2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_IN.value
        ),  # tipo "entrada" pero después del cutoff
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    # La hora manda — el tipo del reloj se ignora.
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_caso_real_K40_4_marcadas_todas_check_out() -> None:
    """Caso reproducible del K40 estándar: el reloj reporta TODAS las
    marcadas como CHECK_OUT. Sub-2.7d resuelve esto correctamente."""
    raws = [
        _raw("2026-04-15T07:55:00", tipo=TipoMarcada.CHECK_OUT.value),
        _raw("2026-04-15T08:02:00", tipo=TipoMarcada.CHECK_OUT.value),  # ruido entrada
        _raw("2026-04-15T16:45:00", tipo=TipoMarcada.CHECK_OUT.value),  # ruido salida
        _raw("2026-04-15T17:05:00", tipo=TipoMarcada.CHECK_OUT.value),
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin, _cutoff_default())
    assert marcadas.entrada == datetime(2026, 4, 15, 7, 55)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 5)


# ── derivar_estado ───────────────────────────────────────────────────────────


def test_derivar_ausente_cuando_no_hay_marcadas() -> None:
    turno = _turno()
    marcadas = MarcadasDelDia(entrada=None, salida=None)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.AUSENTE.value
    assert resultado.hora_entrada_real is None
    assert resultado.hora_salida_real is None
    assert resultado.minutos_tarde == 0
    assert resultado.minutos_salida_temprana == 0


def test_derivar_incompleto_solo_entrada() -> None:
    turno = _turno()
    marcadas = MarcadasDelDia(entrada=datetime(2026, 4, 15, 8, 0), salida=None)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.INCOMPLETO.value
    assert resultado.hora_entrada_real == "08:00:00"
    assert resultado.hora_salida_real is None


def test_derivar_incompleto_solo_salida() -> None:
    turno = _turno()
    marcadas = MarcadasDelDia(entrada=None, salida=datetime(2026, 4, 15, 17, 0))
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.INCOMPLETO.value
    assert resultado.hora_entrada_real is None
    assert resultado.hora_salida_real == "17:00:00"


def test_derivar_presente_exacto() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 0),
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert resultado.minutos_tarde == 0
    assert resultado.minutos_salida_temprana == 0


def test_derivar_presente_dentro_de_tolerancia() -> None:
    """Entrada con 8 min tarde, tolerancia = 10 → PRESENTE."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 8),
        salida=datetime(2026, 4, 15, 16, 57),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    # Pero los minutos se reportan igual (desviación estricta).
    assert resultado.minutos_tarde == 8
    assert resultado.minutos_salida_temprana == 3


def test_derivar_tarde_cuando_supera_tolerancia() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 15),  # 15 min > tol 10
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 15
    assert resultado.minutos_salida_temprana == 0


def test_derivar_salida_temprana() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_salida=5)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 0),
        salida=datetime(2026, 4, 15, 16, 30),  # 30 min antes > tol 5
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.SALIDA_TEMPRANA.value
    assert resultado.minutos_tarde == 0
    assert resultado.minutos_salida_temprana == 30


def test_derivar_tarde_y_salida_temprana() -> None:
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 30),
        salida=datetime(2026, 4, 15, 16, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE_Y_SALIDA_TEMPRANA.value
    assert resultado.minutos_tarde == 30
    assert resultado.minutos_salida_temprana == 60


def test_derivar_turno_nocturno_presente() -> None:
    """Turno de vigilancia 22:00 → 06:00 del día siguiente."""
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
        tol_salida=10,
    )
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 22, 0),
        salida=datetime(2026, 4, 16, 6, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value


def test_derivar_turno_nocturno_tarde() -> None:
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
    )
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 22, 25),  # 25 min tarde
        salida=datetime(2026, 4, 16, 6, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 25


def test_derivar_minutos_se_truncan_a_cero_si_entrada_temprana() -> None:
    """Si el empleado llega antes de la hora oficial, minutos_tarde = 0."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 7, 45),
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.minutos_tarde == 0


# ── estado_no_trabaja ────────────────────────────────────────────────────────


def test_estado_feriado_gana_sobre_sin_turno() -> None:
    turno = _turno(dias=DIAS_LABORALES)
    # 2026-04-11 es sábado (no laborable) + feriado.
    assert estado_no_trabaja(turno, "2026-04-11", es_feriado=True) == EstadoAsistencia.FERIADO.value


def test_estado_feriado_cuando_turno_aplica() -> None:
    turno = _turno(dias=DIAS_LABORALES)
    # 2026-04-15 es miércoles (laboral) pero es feriado.
    assert estado_no_trabaja(turno, "2026-04-15", es_feriado=True) == EstadoAsistencia.FERIADO.value


def test_estado_sin_turno_porque_no_hay_asignacion() -> None:
    assert estado_no_trabaja(None, "2026-04-15", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )


def test_estado_sin_turno_porque_dia_no_aplica() -> None:
    turno = _turno(dias=DIAS_LABORALES)  # lun-vie
    # 2026-04-11 = sábado → no aplica.
    assert estado_no_trabaja(turno, "2026-04-11", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )


def test_estado_dia_aplica_devuelve_none_para_continuar() -> None:
    turno = _turno(dias=DIAS_LABORALES)
    # 2026-04-15 = miércoles → aplica.
    assert estado_no_trabaja(turno, "2026-04-15", es_feriado=False) is None


def test_estado_turno_solo_domingo_sabado() -> None:
    turno = _turno(dias=DIAS_FIN_DE_SEMANA)
    # 2026-04-15 = miércoles → no aplica a turno de fin de semana.
    assert estado_no_trabaja(turno, "2026-04-15", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )
    # 2026-04-11 = sábado → sí aplica.
    assert estado_no_trabaja(turno, "2026-04-11", es_feriado=False) is None


def test_estado_turno_toda_semana_nunca_sin_turno() -> None:
    turno = _turno(dias=DIAS_TODA_LA_SEMANA)
    for fecha in ("2026-04-11", "2026-04-12", "2026-04-13", "2026-04-17"):
        assert estado_no_trabaja(turno, fecha, es_feriado=False) is None


def test_estado_turno_solo_lunes() -> None:
    turno = _turno(dias=LUNES)
    # 2026-04-13 = lunes → aplica.
    assert estado_no_trabaja(turno, "2026-04-13", es_feriado=False) is None
    # 2026-04-14 = martes → no aplica.
    assert estado_no_trabaja(turno, "2026-04-14", es_feriado=False) == (
        EstadoAsistencia.SIN_TURNO.value
    )


# ── Integración ligera algoritmo completo ────────────────────────────────────


def test_flujo_completo_empleado_tarde() -> None:
    """Flujo integrado: ventana → cutoff → parear → derivar, con llegada tarde."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    raws = [
        _raw("2026-04-15T08:15:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_OUT.value),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, cutoff)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 15


def test_flujo_completo_turno_nocturno() -> None:
    """Turno nocturno 22:00-06:00 — cutoff = 23:00 día de entrada.
    Marcada de 22:03 entra como entrada; marcada de 05:58 día siguiente
    cae después del cutoff → es salida."""
    turno = _turno(
        hora_entrada="22:00",
        hora_salida="06:00",
        cruza_medianoche=True,
        tol_entrada=10,
        tol_salida=10,
    )
    raws = [
        _raw("2026-04-15T22:03:00"),
        _raw("2026-04-16T05:58:00"),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, cutoff)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value
    assert resultado.hora_entrada_real == "22:03:00"
    assert resultado.hora_salida_real == "05:58:00"


def test_flujo_completo_empleado_marca_en_varios_relojes() -> None:
    """Un empleado con marcadas de 2 dispositivos distintos se trata igual."""
    turno = _turno()
    raws = [
        RegistroRaw(  # reloj A, entrada
            id=None,
            dispositivo_id=1,
            sincronizacion_id=10,
            zkteco_user_id=42,
            timestamp="2026-04-15T08:00:00",
            tipo_marcada=TipoMarcada.CHECK_IN.value,
        ),
        RegistroRaw(  # reloj B, salida (otro edificio)
            id=None,
            dispositivo_id=2,
            sincronizacion_id=11,
            zkteco_user_id=42,
            timestamp="2026-04-15T17:00:00",
            tipo_marcada=TipoMarcada.CHECK_OUT.value,
        ),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, cutoff)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.PRESENTE.value


def test_flujo_completo_salida_al_dia_siguiente_queda_fuera_de_turno_diurno() -> None:
    """Si una marcada está al dia siguiente y el turno NO cruza medianoche,
    la marcada no entra a la ventana."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00")  # diurno
    raws = [
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-16T02:00:00", tipo=TipoMarcada.CHECK_OUT.value),  # día siguiente
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    cutoff = cutoff_entrada_del_turno(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin, cutoff)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    # Entrada OK pero salida no llegó dentro de la ventana → INCOMPLETO
    assert resultado.estado == EstadoAsistencia.INCOMPLETO.value
    assert resultado.hora_entrada_real == "08:00:00"
    assert resultado.hora_salida_real is None


def test_parear_timestamp_invalido_lanza_value_error() -> None:
    raws = [_raw("not-a-timestamp")]
    cutoff = datetime(2026, 4, 15, 9, 0)
    with pytest.raises(ValueError, match="Timestamp inválido"):
        parear_marcadas(raws, datetime(2026, 4, 15), datetime(2026, 4, 16), cutoff)


# ── Edge: delta de segundos se trunca hacia abajo ────────────────────────────


def test_minutos_tarde_trunca_segundos() -> None:
    """89 segundos tarde → 1 minuto (segundos se truncan con //)."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=0)
    marcadas = MarcadasDelDia(
        entrada=datetime(2026, 4, 15, 8, 1, 29),
        salida=datetime(2026, 4, 15, 17, 0),
    )
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.minutos_tarde == 1
    assert resultado.estado == EstadoAsistencia.TARDE.value


def test_ventana_del_dia_acepta_turno_de_un_segundo() -> None:
    """Sanity: con tolerancias 0 y horas extremas, la ventana sigue siendo válida."""
    turno = _turno(hora_entrada="00:00", hora_salida="23:59", tol_entrada=0, tol_salida=0)
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    assert fin > inicio
    assert (fin - inicio) == timedelta(hours=23, minutes=59)
