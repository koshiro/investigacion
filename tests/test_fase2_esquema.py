"""Reglas de la fase 2 impuestas por la base (0006/0007): cada caso debe fallar cerrado aunque el código se equivoque."""

import itertools

import psycopg
import pytest
from psycopg.types.json import Jsonb

from investigacion import auditoria

SHA = "ab" + "c" * 62
_pasos = itertools.count(1_000_000)
PROHIBIDO = (psycopg.errors.InsufficientPrivilege,)


def _villarrica(su):
    return su.execute("SELECT id FROM investigation WHERE titulo = 'Caminos de Villarrica'").fetchone()[0]


def _org(su, nombre="Municipalidad de Villarrica"):
    return su.execute("SELECT id FROM organization WHERE nombre = %s", (nombre,)).fetchone()[0]


def _captura(su, inv, texto="hola transparencia@villarrica.cl", nivel="ninguno"):
    cap = su.execute(
        "INSERT INTO capture (created_by, investigation_id, url_final, sha256, mime, tamano, ruta, fetched_at) "
        "VALUES ('service:capture', %s, 'https://www.villarrica.cl/t', %s, 'text/html', 3, %s, now()) RETURNING id",
        (inv, SHA, f"evidencia/{SHA[:2]}/{SHA}"),
    ).fetchone()[0]
    ext = su.execute(
        "INSERT INTO extraction (created_by, capture_id, extractor, texto, paginas) "
        "VALUES ('service:extract', %s, 'v1', %s, '[]') RETURNING id",
        (cap, texto),
    ).fetchone()[0]
    pii = su.execute(
        "INSERT INTO pii_scan (created_by, extraction_id, version, nivel, texto_redactado) "
        "VALUES ('service:extract', %s, 'v1', %s, %s) RETURNING id",
        (ext, nivel, texto),
    ).fetchone()[0]
    return cap, pii


def _aprobar(web, tipo, objeto, hash_, decision="aprobada"):
    return web.execute(
        "INSERT INTO approval (created_by, objeto_tipo, objeto_id, content_hash, decision, totp_paso) "
        "VALUES ('daniel', %s, %s, %s, %s, %s) RETURNING id",
        (tipo, objeto, hash_, decision, next(_pasos)),
    ).fetchone()[0]


def _usar(conn, aid, tipo, objeto, hash_, actor="agent:investigador"):
    return conn.execute("SELECT approval_usar(%s, %s, %s, %s, %s)", (aid, tipo, objeto, hash_, actor)).fetchone()[0]


def _plan(worker, inv, contenido=None):
    return worker.execute(
        "INSERT INTO plan (created_by, investigation_id, contenido) VALUES ('agent:investigador', %s, %s) "
        "RETURNING id, content_hash",
        (inv, Jsonb(contenido or {"tareas": [{"rol": "lector", "objetivo": "leer"}]})),
    ).fetchone()


# --- semillas ---------------------------------------------------------------------------------------------------------


def test_semillas_de_villarrica(pg):
    inv = _villarrica(pg.su)
    codigos = [
        r[0] for r in pg.su.execute("SELECT codigo FROM hypothesis WHERE investigation_id = %s ORDER BY 1", (inv,))
    ]
    assert codigos == [f"H{n}" for n in range(1, 8)]
    assert pg.su.execute("SELECT count(*) FROM organization").fetchone()[0] == 4
    assert pg.su.execute("SELECT count(*) FROM organization_contact").fetchone()[0] == 0  # se descubren, no se siembran
    # 0008 los agrega a la lista para el inventario (antes estaban pendientes).
    assert pg.su.execute("SELECT count(*) FROM source_domain WHERE NOT en_lista").fetchone()[0] == 0
    assert (
        pg.su.execute("SELECT count(*) FROM source_domain WHERE estado = 'pendiente'").fetchone()[0] == 18
    )  # + munivillarrica (0009)


# --- approval ---------------------------------------------------------------------------------------------------------


def test_el_worker_no_puede_escribir_approval(pg):
    inv = _villarrica(pg.su)
    w = pg.conectar("inv_worker")
    plan_id, h = _plan(w, inv)
    with pytest.raises(PROHIBIDO):
        _aprobar(w, "plan", plan_id, h)
    aid = _aprobar(pg.conectar("inv_web"), "plan", plan_id, h)
    with pytest.raises(PROHIBIDO):
        w.execute("UPDATE approval SET used_at = now() WHERE id = %s", (aid,))
    with pytest.raises(PROHIBIDO):
        w.execute("DELETE FROM approval")


def test_approval_nace_sin_usar_y_no_se_reescribe(pg):
    inv = _villarrica(pg.su)
    plan_id, h = _plan(pg.conectar("inv_worker"), inv)
    web = pg.conectar("inv_web")
    with pytest.raises(PROHIBIDO):  # web no puede declarar una aprobación ya usada
        web.execute(
            "INSERT INTO approval (created_by, objeto_tipo, objeto_id, content_hash, decision, totp_paso, used_at) "
            "VALUES ('daniel','plan',%s,%s,'aprobada',%s, now())",
            (plan_id, h, next(_pasos)),
        )
    aid = _aprobar(web, "plan", plan_id, h, decision="rechazada")
    for sentencia in ("UPDATE approval SET decision = 'aprobada'", "DELETE FROM approval"):
        with pytest.raises(PROHIBIDO):  # ni siquiera el superusuario
            pg.su.execute(sentencia + " WHERE id = %s", (aid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.su.execute(
            "INSERT INTO approval (created_by, objeto_tipo, objeto_id, content_hash, decision, totp_paso) "
            "VALUES ('agent:investigador','plan',%s,%s,'aprobada',%s)",
            (plan_id, h, next(_pasos)),
        )


def test_un_codigo_totp_sirve_para_una_sola_aprobacion(pg):
    inv = _villarrica(pg.su)
    plan_id, h = _plan(pg.conectar("inv_worker"), inv)
    web = pg.conectar("inv_web")
    sql = (
        "INSERT INTO approval (created_by, objeto_tipo, objeto_id, content_hash, decision, totp_paso) "
        "VALUES ('daniel','plan',%s,%s,'aprobada', 42)"
    )
    web.execute(sql, (plan_id, h))
    with pytest.raises(psycopg.errors.UniqueViolation):
        web.execute(sql, (plan_id, h))


def test_approval_usar_es_de_uso_unico_y_exige_el_hash_exacto(pg):
    inv = _villarrica(pg.su)
    w = pg.conectar("inv_worker")
    plan_id, h = _plan(w, inv)
    otro_id, _ = _plan(w, inv, {"tareas": []})
    web = pg.conectar("inv_web")
    aid = _aprobar(web, "plan", plan_id, h)
    assert _usar(w, aid, "plan", plan_id, "0" * 64) == "el contenido cambió (hash distinto)"
    assert _usar(w, aid, "plan", otro_id, h) == "otro objeto"
    assert _usar(w, aid, "organization_contact", plan_id, h) == "otro objeto"
    assert _usar(w, aid, "plan", plan_id, h) is None
    assert _usar(w, aid, "plan", plan_id, h) == "ya usada"
    rechazada = _aprobar(web, "plan", otro_id, h, decision="rechazada")
    assert _usar(w, rechazada, "plan", otro_id, h) == "rechazada por Daniel"
    vieja = pg.su.execute(
        "INSERT INTO approval (created_by, objeto_tipo, objeto_id, content_hash, decision, totp_paso, vence_at) "
        "VALUES ('daniel','plan',%s,%s,'aprobada',%s, now() - interval '1 second') RETURNING id",
        (plan_id, h, next(_pasos)),
    ).fetchone()[0]
    assert _usar(w, vieja, "plan", plan_id, h) == "vencida"
    acciones = [r[0] for r in pg.su.execute("SELECT accion FROM audit_log WHERE accion LIKE 'approval.%' ORDER BY seq")]
    assert acciones.count("approval.usada") == 1 and acciones.count("approval.rechazada") == 6
    assert auditoria.verificar(pg.su).ok


# --- plan y tareas ----------------------------------------------------------------------------------------------------


def test_una_tarea_no_corre_sin_el_plan_aprobado(pg):
    inv = _villarrica(pg.su)
    w = pg.conectar("inv_worker")
    plan_id, h = _plan(w, inv)
    tid = w.execute(
        "INSERT INTO task (created_by, plan_id, orden, rol, objetivo) VALUES ('agent:investigador',%s,1,'lector','x') "
        "RETURNING id",
        (plan_id,),
    ).fetchone()[0]
    with pytest.raises(PROHIBIDO):
        w.execute("UPDATE task SET estado = 'en_curso' WHERE id = %s", (tid,))
    with pytest.raises(PROHIBIDO):  # tampoco naciendo en curso
        w.execute(
            "INSERT INTO task (created_by, plan_id, orden, rol, objetivo, estado) "
            "VALUES ('agent:investigador',%s,2,'lector','x','en_curso')",
            (plan_id,),
        )
    aid = _aprobar(pg.conectar("inv_web"), "plan", plan_id, h)
    with pytest.raises(PROHIBIDO):  # aprobada pero no usada todavía
        w.execute("UPDATE task SET estado = 'en_curso' WHERE id = %s", (tid,))
    assert _usar(w, aid, "plan", plan_id, h) is None
    w.execute("UPDATE task SET estado = 'en_curso' WHERE id = %s", (tid,))
    with pytest.raises(PROHIBIDO):  # la definición de la tarea no cambia después
        w.execute("UPDATE task SET objetivo = 'otra cosa' WHERE id = %s", (tid,))


def test_el_plan_es_inmutable(pg):
    plan_id, _ = _plan(pg.conectar("inv_worker"), _villarrica(pg.su))
    with pytest.raises(PROHIBIDO):
        pg.su.execute("UPDATE plan SET contenido = '{}' WHERE id = %s", (plan_id,))
    with pytest.raises(PROHIBIDO):
        pg.su.execute("DELETE FROM plan")


# --- contactos --------------------------------------------------------------------------------------------------------


def _contacto(w, org, cap, correo="transparencia@villarrica.cl", estado="candidato"):
    return w.execute(
        "INSERT INTO organization_contact (created_by, organization_id, canal, correo, tipo, capture_id, cita, estado) "
        "VALUES ('agent:investigador',%s,'correo',%s,'transparencia',%s,'escriba a ' || %s, %s) "
        "RETURNING id, content_hash",
        (org, correo, cap, correo, estado),
    ).fetchone()


def test_confirmar_un_contacto_exige_la_aprobacion_de_ese_contenido(pg):
    inv = _villarrica(pg.su)
    cap, _ = _captura(pg.su, inv)
    org = _org(pg.su)
    w, web = pg.conectar("inv_worker"), pg.conectar("inv_web")
    with pytest.raises(PROHIBIDO):
        _contacto(w, org, cap, estado="confirmado")
    cid, h = _contacto(w, org, cap)
    otro, h_otro = _contacto(w, org, cap, correo="oirs@villarrica.cl")
    with pytest.raises(PROHIBIDO):  # el worker no tiene la columna approval_id
        w.execute(
            "UPDATE organization_contact SET estado='confirmado', approval_id=gen_random_uuid() WHERE id=%s", (cid,)
        )
    with pytest.raises(PROHIBIDO):  # el worker no puede confirmar ni siquiera por la columna estado
        w.execute("UPDATE organization_contact SET estado = 'confirmado' WHERE id = %s", (cid,))
    # Aprobación usada, pero de OTRO contacto: no sirve.
    a_otro = _aprobar(web, "organization_contact", otro, h_otro)
    assert _usar(web, a_otro, "organization_contact", otro, h_otro, "daniel") is None
    confirmar = "UPDATE organization_contact SET estado='confirmado', confirmed_at=now(), approval_id=%s WHERE id=%s"
    with pytest.raises(PROHIBIDO):
        web.execute(confirmar, (a_otro, cid))
    a = _aprobar(web, "organization_contact", cid, h)
    with pytest.raises(PROHIBIDO):  # sin usar
        web.execute(confirmar, (a, cid))
    assert _usar(web, a, "organization_contact", cid, h, "daniel") is None
    web.execute(confirmar, (a, cid))
    with pytest.raises(PROHIBIDO):  # el contenido confirmado no se cambia por debajo
        pg.su.execute("UPDATE organization_contact SET correo = 'otro@gmail.com' WHERE id = %s", (cid,))


# --- hallazgos, borradores y capturas aisladas ------------------------------------------------------------------------


def _hallazgo(w, inv, estado="borrador"):
    return w.execute(
        "INSERT INTO finding (created_by, investigation_id, enunciado, tipo, estado) "
        "VALUES ('agent:investigador',%s,'El municipio publicó su presupuesto','hecho',%s) RETURNING id, content_hash",
        (inv, estado),
    ).fetchone()


def _cita(w, fid, cap, pii, texto="hola"):
    return w.execute(
        "INSERT INTO citation (created_by, finding_id, capture_id, pii_scan_id, texto_citado) "
        "VALUES ('agent:investigador',%s,%s,%s,%s) RETURNING id",
        (fid, cap, pii, texto),
    ).fetchone()[0]


def test_un_hallazgo_solo_es_verificado_con_todas_sus_citas_verificadas(pg):
    inv = _villarrica(pg.su)
    cap, pii = _captura(pg.su, inv)
    w, v = pg.conectar("inv_worker"), pg.conectar("inv_verificador")
    with pytest.raises(PROHIBIDO):
        _hallazgo(w, inv, estado="verificado")
    fid, _ = _hallazgo(w, inv)
    with pytest.raises(PROHIBIDO):  # el worker no cambia el estado de un hallazgo
        w.execute("UPDATE finding SET estado = 'verificado' WHERE id = %s", (fid,))
    with pytest.raises(PROHIBIDO):  # sin citas
        v.execute("UPDATE finding SET estado = 'verificado' WHERE id = %s", (fid,))
    c1, c2 = _cita(w, fid, cap, pii), _cita(w, fid, cap, pii)
    v.execute("UPDATE citation SET verified = true, estado = 'verificada' WHERE id = %s", (c1,))
    with pytest.raises(PROHIBIDO):  # una de dos sin verificar
        v.execute("UPDATE finding SET estado = 'verificado' WHERE id = %s", (fid,))
    v.execute("UPDATE citation SET verified = true, estado = 'verificada' WHERE id = %s", (c2,))
    v.execute("UPDATE finding SET estado = 'verificado' WHERE id = %s", (fid,))
    with pytest.raises(PROHIBIDO):
        pg.su.execute("UPDATE finding SET enunciado = 'otra cosa' WHERE id = %s", (fid,))


def test_una_cita_en_revisar_exige_la_aprobacion_del_hallazgo(pg):
    inv = _villarrica(pg.su)
    cap, pii = _captura(pg.su, inv)
    w, v, web = pg.conectar("inv_worker"), pg.conectar("inv_verificador"), pg.conectar("inv_web")
    fid, h = _hallazgo(w, inv)
    c = _cita(w, fid, cap, pii)
    v.execute("UPDATE citation SET estado = 'revisar' WHERE id = %s", (c,))
    with pytest.raises(PROHIBIDO):
        v.execute("UPDATE finding SET estado = 'verificado' WHERE id = %s", (fid,))
    a = _aprobar(web, "finding", fid, h)
    assert _usar(web, a, "finding", fid, h, "daniel") is None
    web.execute("UPDATE finding SET estado = 'verificado' WHERE id = %s", (fid,))


def test_una_cita_tiene_un_solo_dueno(pg):
    inv = _villarrica(pg.su)
    cap, pii = _captura(pg.su, inv)
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.su.execute(
            "INSERT INTO citation (created_by, capture_id, pii_scan_id, texto_citado) VALUES ('x',%s,%s,'t')",
            (cap, pii),
        )


def test_un_borrador_solo_afirma_hallazgos_verificados(pg):
    inv = _villarrica(pg.su)
    w = pg.conectar("inv_worker")
    fid, _ = _hallazgo(w, inv)
    rid = w.execute(
        "INSERT INTO request (created_by, investigation_id, organization_id, asunto, cuerpo) "
        "VALUES ('agent:redactor',%s,%s,'Solicitud','Cuerpo') RETURNING id",
        (inv, _org(pg.su)),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        w.execute(
            "INSERT INTO request_claim (created_by, request_id, finding_id, texto) VALUES ('agent:redactor',%s,%s,'x')",
            (rid, fid),
        )
    with pytest.raises(PROHIBIDO):
        pg.su.execute("UPDATE request SET cuerpo = 'otro' WHERE id = %s", (rid,))


def test_liberar_una_captura_aislada_exige_aprobacion(pg):
    inv = _villarrica(pg.su)
    _, pii = _captura(pg.su, inv, texto="RUN 12.345.678-5", nivel="alto")
    web = pg.conectar("inv_web")
    h = pg.su.execute("SELECT hash_texto(texto_redactado) FROM pii_scan WHERE id = %s", (pii,)).fetchone()[0]
    a = _aprobar(web, "capture_release", pii, h)
    insertar = "INSERT INTO capture_release (created_by, pii_scan_id, approval_id) VALUES ('daniel',%s,%s)"
    with pytest.raises(PROHIBIDO):  # sin usar
        web.execute(insertar, (pii, a))
    assert _usar(web, a, "capture_release", pii, h, "daniel") is None
    web.execute(insertar, (pii, a))
    with pytest.raises(PROHIBIDO):
        pg.conectar("inv_worker").execute(insertar, (pii, a))


def test_el_worker_tiene_su_esquema_dbos_y_nada_mas(pg):
    w = pg.conectar("inv_worker")
    w.execute("CREATE TABLE dbos_worker.prueba (x int)")
    with pytest.raises(PROHIBIDO):
        w.execute("CREATE TABLE public.prueba (x int)")
    with pytest.raises(PROHIBIDO):
        pg.conectar("inv_web").execute("CREATE TABLE dbos_worker.otra (x int)")
