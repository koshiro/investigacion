"""Web: login argon2 + TOTP, sesión de 12 h, CSRF, contenido externo escapado y descargas opacas."""

import secrets

import pyotp
import pytest
from fastapi.testclient import TestClient

from investigacion import auditoria, evidencia
from investigacion.web import Configuracion, auth, crear_app

CLAVE = "k" * 40
# Generadas en cada ejecución: ningún secreto literal en el repo.
PASSWORD = secrets.token_urlsafe(18)
OTRA = secrets.token_urlsafe(18)
NOMBRE = secrets.choice(["daniel"])
CAMPOS = "usuario password codigo".split()


def _formulario(*valores):
    return dict(zip(CAMPOS, valores, strict=True))


@pytest.fixture
def sitio(pg, tmp_path):
    secreto = pyotp.random_base32()
    pg.su.execute(
        "INSERT INTO app_user (created_by, nombre, password_hash) VALUES ('t',%s,%s)", (NOMBRE, auth.hashear(PASSWORD))
    )
    cfg = Configuracion(lambda: pg.conectar("inv_web"), tmp_path, CLAVE, secreto)
    cliente = TestClient(crear_app(cfg), follow_redirects=False)
    return type("Sitio", (), {"c": cliente, "pg": pg, "dir": tmp_path, "secreto": secreto, "cfg": cfg})


def _codigo(sitio, desplazamiento=0):
    return pyotp.TOTP(sitio.secreto).at(__import__("time").time() + 30 * desplazamiento)


def _login(sitio, codigo=None, password=PASSWORD):
    return sitio.c.post("/login", data=_formulario(NOMBRE, password, codigo or _codigo(sitio)))


def _entrar(sitio):
    r = _login(sitio)
    assert r.status_code == 303, r.text
    return sitio.c.get("/").text


def _csrf(sitio):
    html = sitio.c.get("/").text
    return html.split('name="csrf" value="')[1].split('"')[0]


@pytest.mark.parametrize("ruta", ["/", "/auditoria", "/investigaciones/x", "/capturas/x", "/capturas/x/archivo"])
def test_sin_sesion_todo_redirige_al_login(sitio, ruta):
    r = sitio.c.get(ruta)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_correcto_crea_sesion_de_12_horas_con_cookie_segura_de_navegador(sitio):
    r = _login(sitio)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=43200" in cookie
    horas = sitio.pg.su.execute("SELECT extract(epoch FROM (expira_at - now()))/3600 FROM web_session").fetchone()[0]
    assert 11.9 < float(horas) <= 12.0
    # El token no se guarda en claro.
    token = sitio.c.cookies.get("sesion")
    assert sitio.pg.su.execute("SELECT count(*) FROM web_session WHERE token_hash = %s", (token,)).fetchone()[0] == 0


@pytest.mark.parametrize("variante", CAMPOS)
def test_credenciales_incorrectas_no_entran(sitio, variante):
    datos = _formulario(NOMBRE, PASSWORD, _codigo(sitio))
    datos[variante] = "999999" if variante == "codigo" else OTRA
    r = sitio.c.post("/login", data=datos)
    assert r.status_code == 401 and sitio.c.get("/").status_code == 303


def test_un_codigo_totp_no_se_puede_reutilizar(sitio):
    codigo = _codigo(sitio)
    assert _login(sitio, codigo).status_code == 303
    sitio.c.cookies.clear()
    assert _login(sitio, codigo).status_code == 401  # mismo código, misma ventana


def test_totp_valido_es_necesario_aunque_la_contrasena_sea_correcta(sitio):
    assert _login(sitio, "000000").status_code == 401


def test_bloqueo_tras_cinco_fallos_aun_con_credenciales_correctas(sitio):
    for _ in range(5):
        assert _login(sitio, "000000", OTRA).status_code == 401
    assert _login(sitio).status_code == 429
    assert sitio.pg.su.execute("SELECT count(*) FROM audit_log WHERE accion = 'login.bloqueado'").fetchone()[0] >= 1


def test_sesion_vencida_no_sirve(sitio):
    _entrar(sitio)
    sitio.pg.su.execute("UPDATE web_session SET expira_at = now() - interval '1 second'")
    assert sitio.c.get("/").status_code == 303


def test_logout_borra_la_sesion(sitio):
    _entrar(sitio)
    assert sitio.c.post("/logout", data={"csrf": _csrf(sitio)}).status_code == 303
    assert sitio.c.get("/").status_code == 303
    assert sitio.pg.su.execute("SELECT count(*) FROM web_session").fetchone()[0] == 0


def test_login_y_fallos_quedan_en_la_auditoria_y_la_cadena_valida(sitio):
    _login(sitio, "000000")
    _login(sitio)
    acciones = [a for (a,) in sitio.pg.su.execute("SELECT accion FROM audit_log ORDER BY seq")]
    assert acciones == ["login.fallo", "login.ok"] and auditoria.verificar(sitio.pg.su).ok


def test_post_sin_csrf_o_con_csrf_ajeno_se_rechaza(sitio):
    _entrar(sitio)
    assert sitio.c.post("/investigaciones", data={"titulo": "t", "pregunta": "p"}).status_code == 422
    assert sitio.c.post("/investigaciones", data={"titulo": "t", "pregunta": "p", "csrf": "x" * 40}).status_code == 403
    assert sitio.pg.su.execute("SELECT count(*) FROM investigation").fetchone()[0] == 0


def test_crear_investigacion_y_pedir_captura(sitio):
    _entrar(sitio)
    csrf = _csrf(sitio)
    r = sitio.c.post(
        "/investigaciones", data={"titulo": "Caminos Villarrica", "pregunta": "¿Cuánto se invirtió?", "csrf": csrf}
    )
    assert r.status_code == 303
    iid = r.headers["location"].rsplit("/", 1)[1]
    assert "Caminos Villarrica" in sitio.c.get("/").text
    r = sitio.c.post(f"/investigaciones/{iid}/capturar", data={"url": "https://www.villarrica.cl/a.pdf", "csrf": csrf})
    assert r.status_code == 303
    assert sitio.pg.su.execute("SELECT url, estado FROM capture_job").fetchone() == (
        "https://www.villarrica.cl/a.pdf",
        "pendiente",
    )
    assert (
        sitio.c.post(f"/investigaciones/{iid}/capturar", data={"url": "file:///etc/passwd", "csrf": csrf}).status_code
        == 422
    )


def _con_captura(sitio, contenido: bytes, mime="text/html"):
    from investigacion import extract

    inv = sitio.pg.su.execute(
        "INSERT INTO investigation (created_by, titulo, pregunta) VALUES ('d','Inv','p') RETURNING id"
    ).fetchone()[0]
    g = evidencia.guardar_bytes(sitio.dir, contenido)
    cid = sitio.pg.su.execute(
        "INSERT INTO capture (created_by, investigation_id, url_final, sha256, mime, tamano, ruta, fetched_at, estado_http) "
        "VALUES ('service:capture', %s, 'https://x.cl/a', %s, %s, %s, %s, now(), 200) RETURNING id",
        (inv, g.sha256, mime, g.tamano, g.ruta),
    ).fetchone()[0]
    extract.procesar_capturas(sitio.pg.conectar("inv_extract"), sitio.dir)
    return str(inv), str(cid), g


def test_visor_escapa_contenido_externo_y_muestra_original_y_redactado(sitio):
    html = b"<html><body><p>Escribir a <script>alert(1)</script> pedro@gmail.com monto $ 1.000</p></body></html>"
    _, cid, _ = _con_captura(sitio, html)
    _entrar(sitio)
    orig = sitio.c.get(f"/capturas/{cid}").text
    assert "<script>alert(1)</script>" not in orig  # el HTML de la fuente nunca llega crudo (se extrae a texto)
    assert "pedro@gmail.com" in orig
    red = sitio.c.get(f"/capturas/{cid}?version=redactado").text
    assert "pedro@gmail.com" not in red and "[CORREO_1]" in red


def test_visor_escapa_html_en_texto_plano(sitio):
    _, cid, _ = _con_captura(sitio, b"<img src=x onerror=alert(1)> monto $ 1.000", mime="text/plain")
    _entrar(sitio)
    pagina = sitio.c.get(f"/capturas/{cid}").text
    assert "&lt;img src=x onerror=alert(1)&gt;" in pagina and "<img src=x" not in pagina


def test_descarga_es_opaca_y_se_audita(sitio):
    _, cid, _ = _con_captura(sitio, b"<script>x</script>", mime="text/html")
    _entrar(sitio)
    r = sitio.c.get(f"/capturas/{cid}/archivo")
    assert r.content == b"<script>x</script>"
    assert r.headers["content-type"] == "application/octet-stream" and "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert sitio.pg.su.execute("SELECT count(*) FROM audit_log WHERE accion = 'capture.descargar'").fetchone()[0] == 1


def test_cabeceras_de_seguridad_y_sin_documentacion_expuesta(sitio):
    r = sitio.c.get("/login")
    assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert sitio.c.get("/docs").status_code == 404 and sitio.c.get("/openapi.json").status_code == 404


def test_verificar_hash_detecta_un_archivo_alterado(sitio):
    _, cid, g = _con_captura(sitio, b"contenido original de la captura 1")
    _entrar(sitio)
    csrf = _csrf(sitio)
    assert "Íntegra" in sitio.c.post(f"/capturas/{cid}/verificar", data={"csrf": csrf}).text
    archivo = evidencia.ruta_absoluta(sitio.dir, g.sha256)
    archivo.chmod(0o644)
    archivo.write_bytes(b"manipulado")
    assert "ALTERADA" in sitio.c.post(f"/capturas/{cid}/verificar", data={"csrf": csrf}).text
    assert "ALTERADA" in sitio.c.get(f"/capturas/{cid}").text


def test_pagina_de_auditoria_valida_la_cadena_y_delata_alteraciones(sitio):
    _, _cid, _ = _con_captura(sitio, b"texto de prueba suficiente para la captura")
    _entrar(sitio)
    assert "válida" in sitio.c.get("/auditoria").text
    sitio.pg.su.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_inmutable")
    sitio.pg.su.execute("UPDATE audit_log SET accion = 'otra' WHERE seq = 1")
    pagina = sitio.c.get("/auditoria").text
    assert "ROTA" in pagina


def test_captura_aislada_se_muestra_con_aviso(sitio):
    _, cid, _ = _con_captura(sitio, b"Vecino RUN 12.345.678-5 reclama.", mime="text/plain")
    _entrar(sitio)
    pagina = sitio.c.get(f"/capturas/{cid}").text
    assert "Captura aislada" in pagina
