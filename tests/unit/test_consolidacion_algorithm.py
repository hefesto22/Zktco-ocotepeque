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
    MarcadasDelDia,
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


# ── parear_marcadas ──────────────────────────────────────────────────────────


def test_parear_ambos_explicitos_usa_primer_in_y_ultimo_out() -> None:
    raws = [
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T12:00:00", tipo=TipoMarcada.CHECK_OUT.value),  # salida a almorzar
        _raw("2026-04-15T13:00:00", tipo=TipoMarcada.CHECK_IN.value),  # vuelve
        _raw("2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_OUT.value),  # salida final
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_solo_unknowns_usa_primera_y_ultima() -> None:
    raws = [
        _raw("2026-04-15T07:55:00"),
        _raw("2026-04-15T12:00:00"),
        _raw("2026-04-15T17:05:00"),
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 7, 55)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 5)


def test_parear_mezcla_prefiere_explicitos_sobre_unknown() -> None:
    """Si hay CHECK_IN/OUT explícitos, los UNKNOWN no pisan."""
    raws = [
        _raw("2026-04-15T07:30:00"),  # UNKNOWN temprano — NO debe ser entrada
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_OUT.value),
        _raw("2026-04-15T17:30:00"),  # UNKNOWN tardío — NO debe ser salida
    ]
    inicio = datetime(2026, 4, 15, 7, 0)
    fin = datetime(2026, 4, 15, 18, 0)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_overtime_tambien_es_entrada_y_salida() -> None:
    raws = [
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.OVERTIME_IN.value),
        _raw("2026-04-15T20:00:00", tipo=TipoMarcada.OVERTIME_OUT.value),
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 21, 0)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 20, 0)


def test_parear_solo_una_unknown_no_se_reutiliza() -> None:
    """Una única marcada UNKNOWN se asigna a entrada, la salida queda en None."""
    raws = [_raw("2026-04-15T08:00:00")]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida is None


def test_parear_descarta_marcadas_fuera_de_ventana() -> None:
    raws = [
        _raw("2026-04-14T23:00:00"),  # día anterior, fuera
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_OUT.value),
        _raw("2026-04-15T20:00:00"),  # UNKNOWN después del fin de ventana, fuera
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


def test_parear_sin_marcadas_en_ventana_retorna_ambos_none() -> None:
    raws = [_raw("2026-04-14T23:00:00"), _raw("2026-04-16T01:00:00")]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada is None
    assert marcadas.salida is None


def test_parear_solo_check_in_sin_check_out() -> None:
    """Empleado marcó entrada pero olvidó marcar salida (todo explícito)."""
    raws = [_raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value)]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida is None


def test_parear_check_in_explicito_mas_unknown_tardio_usa_unknown_como_salida() -> None:
    """Fallback: hay CHECK_IN pero no CHECK_OUT → UNKNOWN tardío cubre salida."""
    raws = [
        _raw("2026-04-15T08:00:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T17:00:00"),  # UNKNOWN — usada como salida vía fallback
    ]
    inicio = datetime(2026, 4, 15, 7, 50)
    fin = datetime(2026, 4, 15, 17, 30)
    marcadas = parear_marcadas(raws, inicio, fin)
    assert marcadas.entrada == datetime(2026, 4, 15, 8, 0)
    assert marcadas.salida == datetime(2026, 4, 15, 17, 0)


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
    """Flujo integrado: ventana → parear → derivar, con llegada tarde."""
    turno = _turno(hora_entrada="08:00", hora_salida="17:00", tol_entrada=10, tol_salida=5)
    raws = [
        _raw("2026-04-15T08:15:00", tipo=TipoMarcada.CHECK_IN.value),
        _raw("2026-04-15T17:00:00", tipo=TipoMarcada.CHECK_OUT.value),
    ]
    inicio, fin = ventana_del_dia(turno, "2026-04-15")
    marcadas = parear_marcadas(raws, inicio, fin)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    assert resultado.estado == EstadoAsistencia.TARDE.value
    assert resultado.minutos_tarde == 15


def test_flujo_completo_turno_nocturno_solo_unknowns() -> None:
    """Vigilancia con reloj que no reporta tipo — solo UNKNOWN."""
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
    marcadas = parear_marcadas(raws, inicio, fin)
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
    marcadas = parear_marcadas(raws, inicio, fin)
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
    marcadas = parear_marcadas(raws, inicio, fin)
    resultado = derivar_estado(marcadas, turno, "2026-04-15")
    # Entrada OK pero salida no llegó dentro de la ventana → INCOMPLETO
    assert resultado.estado == EstadoAsistencia.INCOMPLETO.value
    assert resultado.hora_entrada_real == "08:00:00"
    assert resultado.hora_salida_real is None


def test_parear_timestamp_invalido_lanza_value_error() -> None:
    raws = [_raw("not-a-timestamp")]
    with pytest.raises(ValueError, match="Timestamp inválido"):
        parear_marcadas(raws, datetime(2026, 4, 15), datetime(2026, 4, 16))


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
