"""La cadena de auditoría valida de punta a punta y cualquier alteración se detecta (regla no negociable 7)."""

import psycopg
import pytest

from investigacion import auditoria


def _llenar(conn, n=5):
    for i in range(n):
        auditoria.registrar(conn, "service:test", "prueba.paso", f"obj/{i}", {"i": i, "acentos": "ñandú ✓"})


def test_cadena_valida_de_punta_a_punta(pg):
    _llenar(pg.su, 8)
    r = auditoria.verificar(pg.su)
    assert r.ok and r.filas == 8 and len(r.hash_final) == 64


def test_cadena_vacia_es_valida(pg):
    assert auditoria.verificar(pg.su).ok


def test_sql_y_python_calculan_el_mismo_hash(pg):
    # Si divergieran, verificar() fallaría en la primera fila; este caso incluye jsonb con claves reordenadas.
    auditoria.registrar(pg.su, "daniel", "x", "y", {"z": 1, "a": [1, 2, {"b": None}]})
    assert auditoria.verificar(pg.su).ok


def test_modificar_una_fila_rompe_la_cadena(pg):
    _llenar(pg.su)
    # El dueño no puede (trigger); un superusuario que desactiva el trigger sí, y la cadena lo delata.
    pg.su.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_inmutable")
    pg.su.execute("UPDATE audit_log SET accion = 'otra' WHERE seq = 3")
    r = auditoria.verificar(pg.su)
    assert not r.ok and r.primera_rota == 3


def test_borrar_una_fila_rompe_la_cadena(pg):
    _llenar(pg.su)
    pg.su.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_inmutable")
    pg.su.execute("DELETE FROM audit_log WHERE seq = 2")
    r = auditoria.verificar(pg.su)
    assert not r.ok and r.primera_rota == 3


def test_reescribir_una_fila_y_su_hash_rompe_la_siguiente(pg):
    _llenar(pg.su)
    pg.su.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_inmutable")
    nuevo = auditoria.calcular_hash(
        auditoria.GENESIS,
        1,
        pg.su.execute(
            "SELECT to_char(ts AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US') FROM audit_log WHERE seq=1"
        ).fetchone()[0],
        "service:test",
        "prueba.paso",
        "obj/0",
        '{"x": 1}',
    )
    pg.su.execute("UPDATE audit_log SET datos = '{\"x\": 1}', hash = %s WHERE seq = 1", (nuevo,))
    r = auditoria.verificar(pg.su)
    assert not r.ok and r.primera_rota == 2


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET accion = 'x'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_nadie_modifica_ni_borra_la_auditoria(pg, sql):
    _llenar(pg.su, 2)
    # Ni siquiera el dueño/superusuario: el trigger falla cerrado.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.su.execute(sql)
    assert auditoria.verificar(pg.su).filas == 2


@pytest.mark.parametrize(
    "rol", ["inv_web", "inv_capture", "inv_extract", "inv_worker", "inv_gateway", "inv_verificador"]
)
def test_roles_de_servicio_solo_agregan_por_la_funcion(pg, rol):
    c = pg.conectar(rol)
    auditoria.registrar(c, f"service:{rol}", "prueba", "x")  # permitido
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(
            "INSERT INTO audit_log (seq, actor, accion, objeto, prev_hash, hash) VALUES (99,'a','b','c',repeat('0',64),repeat('1',64))"
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("UPDATE audit_log SET accion = 'x'")
    assert auditoria.verificar(pg.su).ok


def test_inserciones_concurrentes_no_bifurcan_la_cadena(pg):
    a, b = pg.conectar(), pg.conectar()
    for i in range(10):
        auditoria.registrar(a if i % 2 else b, "service:test", "alterna", str(i))
    assert auditoria.verificar(pg.su).filas == 10
