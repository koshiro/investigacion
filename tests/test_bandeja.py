"""Bandeja de aprobaciones: TOTP nuevo en cada decisión, contenido exacto ligado al hash y efectos de cada tipo."""

import re

import pyotp
from psycopg.types.json import Jsonb

from investigacion import auditoria
from tests.test_fase2_esquema import _captura, _org, _villarrica
from tests.test_web import _codigo, _csrf, _entrar


def _codigo_nuevo(sitio):
    """Simula que pasó una ventana de 30 s desde el último código usado."""
    sitio.pg.su.execute("UPDATE app_user SET totp_ultimo_paso = 0")
    return pyotp.TOTP(sitio.secreto).now()


def _plan(sitio, contenido=None, anterior=None):
    w = sitio.pg.conectar("inv_worker")
    return w.execute(
        "INSERT INTO plan (created_by, investigation_id, anterior_id, contenido) VALUES ('agent:investigador',%s,%s,%s) "
        "RETURNING id::text, content_hash",
        (_villarrica(sitio.pg.su), anterior, Jsonb(contenido or {"tareas": [{"rol": "lector", "objetivo": "leer"}]})),
    ).fetchone()


def _decidir(sitio, tipo, oid, h, codigo, decision="aprobada"):
    datos = {"content_hash": h, "decision": decision, "totp": codigo, "csrf": _csrf(sitio)}
    return sitio.c.post(f"/aprobaciones/{tipo}/{oid}", data=datos)


def _aprobaciones(sitio):
    return sitio.pg.su.execute("SELECT objeto_tipo, objeto_id::text, decision, used_at FROM approval").fetchall()


def test_el_plan_aparece_con_su_contenido_exacto_y_su_hash(sitio):
    _entrar(sitio)
    pid, h = _plan(sitio)
    assert f"/aprobaciones/plan/{pid}" in sitio.c.get("/aprobaciones").text
    html = sitio.c.get(f"/aprobaciones/plan/{pid}").text
    assert h in html and "&#34;objetivo&#34;: &#34;leer&#34;" in html
    # Usuario y TOTP en el mismo formulario: el gestor de contraseñas propone el código de esa cuenta.
    assert 'autocomplete="username" value="daniel"' in html and 'id="totp" name="totp"' in html


def test_aprobar_exige_un_totp_nuevo_cada_vez(sitio):
    _entrar(sitio)
    pid, h = _plan(sitio)
    assert _decidir(sitio, "plan", pid, h, "000000").status_code == 401
    assert _decidir(sitio, "plan", pid, h, _codigo(sitio)).status_code == 401  # el mismo código del login
    assert _aprobaciones(sitio) == []
    assert _decidir(sitio, "plan", pid, h, _codigo(sitio, 1)).status_code == 303
    otro, h2 = _plan(sitio, {"tareas": []})
    assert _decidir(sitio, "plan", otro, h2, _codigo(sitio, 1)).status_code == 401  # ya usado
    assert [(t, d) for t, _, d, _ in _aprobaciones(sitio)] == [("plan", "aprobada")]
    acciones = [a for (a,) in sitio.pg.su.execute("SELECT accion FROM audit_log ORDER BY seq")]
    assert acciones.count("approval.totp_fallo") == 3 and "approval.decidida" in acciones
    assert auditoria.verificar(sitio.pg.su).ok


def test_si_el_contenido_cambio_la_aprobacion_se_rechaza(sitio):
    _entrar(sitio)
    pid, _ = _plan(sitio)
    r = _decidir(sitio, "plan", pid, "f" * 64, _codigo(sitio, 1))
    assert r.status_code == 409 and "cambió" in r.text
    assert _aprobaciones(sitio) == []


def test_el_plan_aprobado_queda_sin_usar_para_el_worker_y_sale_de_la_bandeja(sitio):
    _entrar(sitio)
    pid, h = _plan(sitio)
    assert _decidir(sitio, "plan", pid, h, _codigo(sitio, 1)).status_code == 303
    assert _aprobaciones(sitio) == [("plan", pid, "aprobada", None)]
    assert f"/aprobaciones/plan/{pid}" not in sitio.c.get("/aprobaciones").text.split("Últimas decisiones")[0]


def test_una_version_nueva_del_plan_muestra_el_diff(sitio):
    _entrar(sitio)
    viejo, _ = _plan(sitio, {"tareas": [{"objetivo": "leer el BIP"}]})
    nuevo, _ = _plan(sitio, {"tareas": [{"objetivo": "leer el BIP y SINIM"}]}, anterior=viejo)
    html = sitio.c.get(f"/aprobaciones/plan/{nuevo}").text
    assert re.search(r"\n-\s+&#34;objetivo&#34;: &#34;leer el BIP&#34;", html)
    assert re.search(r"\n\+\s+&#34;objetivo&#34;: &#34;leer el BIP y SINIM&#34;", html)
    bandeja = sitio.c.get("/aprobaciones").text
    assert nuevo in bandeja and viejo not in bandeja


def test_confirmar_un_canal_lo_marca_confirmado_y_deja_obsoleto_el_anterior(sitio):
    _entrar(sitio)
    su = sitio.pg.su
    cap, _ = _captura(su, _villarrica(su))
    w = sitio.pg.conectar("inv_worker")
    insertar = (
        "INSERT INTO organization_contact (created_by, organization_id, canal, correo, tipo, capture_id, cita) "
        "VALUES ('agent:investigador',%s,'correo',%s,'transparencia',%s,%s) RETURNING id::text, content_hash"
    )
    org = _org(su)
    viejo, hv = w.execute(insertar, (org, "antiguo@villarrica.cl", cap, "antiguo@villarrica.cl")).fetchone()
    assert _decidir(sitio, "organization_contact", viejo, hv, _codigo(sitio, 1)).status_code == 303
    cid, h = w.execute(insertar, (org, "transparencia@villarrica.cl", cap, "<script>x</script>")).fetchone()
    html = sitio.c.get(f"/aprobaciones/organization_contact/{cid}").text
    assert "<script>x" not in html and "&lt;script&gt;x" in html
    assert _decidir(sitio, "organization_contact", cid, h, _codigo_nuevo(sitio)).status_code == 303
    estados = dict(su.execute("SELECT correo, estado FROM organization_contact").fetchall())
    assert estados == {"antiguo@villarrica.cl": "obsoleto", "transparencia@villarrica.cl": "confirmado"}
    assert all(usada is not None for *_, usada in _aprobaciones(sitio))


def test_rechazar_un_canal_lo_descarta(sitio):
    _entrar(sitio)
    su = sitio.pg.su
    cap, _ = _captura(su, _villarrica(su))
    cid, h = (
        sitio.pg.conectar("inv_worker")
        .execute(
            "INSERT INTO organization_contact (created_by, organization_id, canal, correo, tipo, capture_id, cita) "
            "VALUES ('agent:investigador',%s,'correo','x@gmail.com','oirs',%s,'x@gmail.com') "
            "RETURNING id::text, content_hash",
            (_org(su), cap),
        )
        .fetchone()
    )
    assert _decidir(sitio, "organization_contact", cid, h, _codigo(sitio, 1), "rechazada").status_code == 303
    assert su.execute("SELECT estado FROM organization_contact").fetchone()[0] == "descartado"


def test_liberar_una_captura_aislada(sitio):
    _entrar(sitio)
    su = sitio.pg.su
    _, pii = _captura(su, _villarrica(su), texto="[RUN_1] pidió el informe", nivel="alto")
    assert f"/aprobaciones/capture_release/{pii}" in sitio.c.get("/aprobaciones").text
    h = su.execute("SELECT hash_texto(texto_redactado) FROM pii_scan WHERE id = %s", (pii,)).fetchone()[0]
    assert _decidir(sitio, "capture_release", pii, h, _codigo(sitio, 1)).status_code == 303
    assert su.execute("SELECT count(*) FROM capture_release WHERE pii_scan_id = %s", (pii,)).fetchone()[0] == 1


def test_tipo_desconocido_o_id_invalido(sitio):
    _entrar(sitio)
    assert sitio.c.get("/aprobaciones/proxy_domain/00000000-0000-0000-0000-000000000000").status_code == 404
    assert sitio.c.get("/aprobaciones/plan/no-es-uuid").status_code == 422
    assert sitio.c.get("/aprobaciones").status_code == 200


def test_no_se_pide_un_segundo_plan_mientras_hay_uno_en_curso(sitio):
    _entrar(sitio)
    inv = _villarrica(sitio.pg.su)
    assert sitio.c.post(f"/investigaciones/{inv}/plan", data={"csrf": _csrf(sitio)}).status_code == 303
    assert sitio.c.post(f"/investigaciones/{inv}/plan", data={"csrf": _csrf(sitio)}).status_code == 409
    assert sitio.pg.su.execute("SELECT count(*) FROM plan_request").fetchone()[0] == 1
