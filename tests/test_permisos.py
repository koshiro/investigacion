"""Permisos mínimos por rol y tablas inmutables (reglas 3, 4 y 7): cada caso debe fallar cerrado."""

import uuid

import psycopg
import pytest

SHA = "ab" + "c" * 62


def _sembrar(su):
    inv = su.execute(
        "INSERT INTO investigation (created_by, titulo, pregunta) VALUES ('daniel','t','p') RETURNING id"
    ).fetchone()[0]
    cap = su.execute(
        "INSERT INTO capture (created_by, investigation_id, url_final, sha256, mime, tamano, ruta, fetched_at) "
        "VALUES ('service:capture', %s, 'https://x', %s, 'text/plain', 3, %s, now()) RETURNING id",
        (inv, SHA, f"evidencia/{SHA[:2]}/{SHA}"),
    ).fetchone()[0]
    ext = su.execute(
        "INSERT INTO extraction (created_by, capture_id, extractor, texto, paginas) VALUES ('service:extract', %s, 'v1', 'hola', '[]') RETURNING id",
        (cap,),
    ).fetchone()[0]
    pii = su.execute(
        "INSERT INTO pii_scan (created_by, extraction_id, version, nivel, texto_redactado) VALUES ('service:extract', %s, 'v1', 'ninguno', 'hola') RETURNING id",
        (ext,),
    ).fetchone()[0]
    card = su.execute(
        "INSERT INTO card (created_by, capture_id, pii_scan_id, agente, esquema, contenido) VALUES ('agent:lector', %s, %s, 'lector', 'v1', '{}') RETURNING id",
        (cap, pii),
    ).fetchone()[0]
    return cap, pii, card


@pytest.mark.parametrize("tabla", ["capture", "extraction", "pii_scan", "card", "llm_call"])
@pytest.mark.parametrize("sentencia", ["UPDATE {t} SET created_by = 'x'", "DELETE FROM {t}", "TRUNCATE {t}"])
def test_tablas_inmutables_incluso_para_el_superusuario(pg, tabla, sentencia):
    _sembrar(pg.su)
    pg.su.execute(
        "INSERT INTO llm_call (created_by, rol, proveedor, modelo, hash_prompt) VALUES ('service:llm-gateway','lector','anthropic','m',repeat('0',64))"
    )
    # TRUNCATE sobre una tabla con claves foráneas entrantes falla antes por FeatureNotSupported: también es cerrado.
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.FeatureNotSupported)):
        pg.su.execute(sentencia.format(t=tabla))


def test_modificar_una_captura_desde_un_rol_de_servicio_falla(pg):
    _sembrar(pg.su)
    for rol in ("inv_capture", "inv_web", "inv_worker"):
        c = pg.conectar(rol)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE capture SET sha256 = repeat('0', 64)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM capture")


def test_la_ruta_de_la_captura_debe_ser_la_del_hash(pg):
    inv = pg.su.execute(
        "INSERT INTO investigation (created_by, titulo, pregunta) VALUES ('d','t','p') RETURNING id"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.su.execute(
            "INSERT INTO capture (created_by, investigation_id, url_final, sha256, mime, tamano, ruta, fetched_at) "
            "VALUES ('x', %s, 'u', %s, 'm', 1, 'evidencia/zz/otra', now())",
            (inv, SHA),
        )


def test_el_worker_no_puede_marcar_una_cita_como_verificada(pg):
    cap, pii, card = _sembrar(pg.su)
    w = pg.conectar("inv_worker")
    cid = w.execute(
        "INSERT INTO citation (created_by, card_id, capture_id, pii_scan_id, texto_citado) VALUES ('agent:lector', %s, %s, %s, 'hola') RETURNING id",
        (card, cap, pii),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        w.execute("UPDATE citation SET verified = true, estado = 'verificada' WHERE id = %s", (cid,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        w.execute(
            "INSERT INTO citation (created_by, card_id, capture_id, pii_scan_id, texto_citado, verified, estado) "
            "VALUES ('agent:lector', %s, %s, %s, 'x', true, 'verificada')",
            (card, cap, pii),
        )
    v = pg.conectar("inv_verificador")
    v.execute("UPDATE citation SET verified = true, estado = 'verificada', verificador = 'v1' WHERE id = %s", (cid,))
    assert pg.su.execute("SELECT verified FROM citation WHERE id = %s", (cid,)).fetchone()[0] is True


def test_una_cita_no_puede_ser_verified_sin_estado_verificada(pg):
    cap, pii, card = _sembrar(pg.su)
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.su.execute(
            "INSERT INTO citation (created_by, card_id, capture_id, pii_scan_id, texto_citado, verified, estado) "
            "VALUES ('x', %s, %s, %s, 't', true, 'revisar')",
            (card, cap, pii),
        )


def test_las_citas_no_se_borran(pg):
    cap, pii, card = _sembrar(pg.su)
    pg.su.execute(
        "INSERT INTO citation (created_by, card_id, capture_id, pii_scan_id, texto_citado) VALUES ('x', %s, %s, %s, 't')",
        (card, cap, pii),
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.su.execute("DELETE FROM citation")


def test_gateway_extract_y_capture_tienen_alcance_acotado(pg):
    _sembrar(pg.su)
    gw = pg.conectar("inv_gateway")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        gw.execute("SELECT * FROM capture")
    ex = pg.conectar("inv_extract")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ex.execute(
            "INSERT INTO capture (created_by, url_final, sha256, mime, tamano, ruta, fetched_at) VALUES ('x','u',repeat('a',64),'m',1,'r',now())"
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ex.execute("SELECT * FROM llm_call")
    cp = pg.conectar("inv_capture")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cp.execute("SELECT * FROM pii_scan")
    assert uuid.UUID(str(pg.su.execute("SELECT gen_random_uuid()").fetchone()[0]))
