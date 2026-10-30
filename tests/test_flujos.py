"""Flujos DBOS de verdad: Postgres, esquema dbos_worker y un login que solo tiene inv_worker (como usr_worker).

- El plan espera la aprobación de Daniel; sin ella ninguna tarea corre.
- Al aprobar, el despertador despierta al flujo, que consume la aprobación y ejecuta; un paso fuera de rol se cancela
  y queda auditado.
- Descubrimiento e inventario con capturas simuladas (el servicio capture no corre en las pruebas).
"""

import json
import secrets
import threading
import time
from urllib.parse import quote

import httpx
import psycopg
import psycopg.conninfo
import pytest
from dbos import DBOS
from psycopg.types.json import Jsonb

from investigacion import aprobaciones, auditoria, flujos, gateway
from investigacion.buscador import Buscador
from tests.fakes import FakeAnthropic, texto
from tests.test_fase2_esquema import _villarrica

SHA = "cd" + "e" * 62


def _login(pg, nombre: str, grupo: str) -> str:
    """Login de prueba con clave generada en ejecución (en CI Postgres va por TCP con contraseña)."""
    clave = secrets.token_hex(16)
    pg.su.execute(
        f"DO $$ BEGIN CREATE ROLE {nombre} LOGIN IN ROLE {grupo}; EXCEPTION WHEN duplicate_object THEN NULL; END $$"  # type: ignore[arg-type]
    )
    pg.su.execute(f"ALTER ROLE {nombre} PASSWORD '{clave}'")  # type: ignore[arg-type]
    # DBOS (SQLAlchemy) exige URL, no DSN clave=valor: como el URI de CNPG en el cluster.
    d = psycopg.conninfo.conninfo_to_dict(pg.url)
    host, puerto, base = str(d.get("host") or "localhost"), d.get("port") or "5432", d["dbname"]
    if host.startswith("/"):
        return f"postgresql://{nombre}:{clave}@/{base}?host={quote(host)}&port={puerto}"
    return f"postgresql://{nombre}:{clave}@{host}:{puerto}/{base}"


def _resultado(wf: str, plazo_s: float = 60):
    """get_result con plazo: si el flujo se cuelga, la prueba falla en vez de bloquear el CI."""
    fin = time.monotonic() + plazo_s
    h = DBOS.retrieve_workflow(wf)
    while h.get_status().status in ("PENDING", "ENQUEUED"):
        assert time.monotonic() < fin, f"flujo {wf} sigue en {h.get_status().status}"
        time.sleep(0.2)
    return h.get_result()


PLAN = {
    "objetivo": "Prueba",
    "pasos": [
        {"rol": "investigador", "herramienta": "query_readonly", "argumentos": [{"nombre": "vista", "valor": "hipotesis"}],
         "objetivo": "ver hipótesis"},
        {"rol": "lector", "herramienta": "search_web", "argumentos": [{"nombre": "consulta", "valor": "x"}],
         "objetivo": "fuera de rol"},
    ],
}  # fmt: skip


@pytest.fixture
def worker(pg, monkeypatch):
    monkeypatch.setattr(flujos, "ESPERA_APROBACION_S", 2)
    monkeypatch.setattr(flujos, "ESPERA_CAPTURAS_S", 0.2)
    url_w = _login(pg, "t_worker", "inv_worker")
    url_v = _login(pg, "t_verificador", "inv_verificador")
    fake = FakeAnthropic(texto(json.dumps(PLAN)))
    modelos = {r: "claude-sonnet-5-5" for r in ("lector", "investigador", "redactor", "verificador")}
    gw = gateway.Gateway(fake, pg.conectar("inv_gateway"), modelos)
    resultados = {"results": [{"title": "Transparencia", "url": "https://www.munivillarrica.cl/transparencia"}]}
    busc = Buscador(
        "http://searxng:8080", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=resultados))
    )
    entorno = flujos.Entorno(url_w, url_v, gw, busc, {}, tiene_mx=lambda d: True, resolver=lambda d: d != "www.bcn.cl")
    flujos.iniciar(url_w, entorno)  # el cliente real de DBOS con el rol del worker
    yield type("W", (), {"url": url_w, "pg": pg, "fake": fake})
    DBOS.destroy(destroy_registry=False)


def _pedir_plan(pg):
    inv = str(_villarrica(pg.su))
    pg.conectar("inv_web").execute(
        "INSERT INTO plan_request (created_by, investigation_id) VALUES ('daniel', %s)", (inv,)
    )
    with psycopg.connect(pg.url, autocommit=True) as c:
        c.execute("SET ROLE inv_worker")
        assert flujos.iniciar_pedidos(c) == 1
        assert flujos.iniciar_pedidos(c) == 0  # idempotente
    wf = pg.su.execute("SELECT workflow_id FROM plan_request").fetchone()[0]
    plan_id = DBOS.get_event(wf, "plan_id", timeout_seconds=30)
    assert plan_id
    return inv, wf, plan_id


def _decidir(pg, plan_id, decision):
    web = pg.conectar("inv_web")
    obj = aprobaciones.cargar(web, "plan", plan_id)
    aprobaciones.decidir(web, obj, obj.hash, decision, totp_paso=int(time.time()))


def _despertar(pg):
    with psycopg.connect(pg.url, autocommit=True) as c:
        c.execute("SET ROLE inv_worker")
        return flujos.despertar(c)


def test_sin_aprobacion_no_corre_nada_y_al_aprobar_corre_lo_permitido(worker):
    pg = worker.pg
    _, wf, plan_id = _pedir_plan(pg)
    contenido = pg.su.execute("SELECT contenido FROM plan WHERE id = %s", (plan_id,)).fetchone()[0]
    assert contenido["problemas_de_permisos"] == ["paso 2: el rol lector no puede usar search_web"]
    time.sleep(3)  # más que una espera de recv: el flujo sigue esperando
    estados = [r[0] for r in pg.su.execute("SELECT estado FROM task ORDER BY orden")]
    assert estados == ["propuesta", "propuesta"]
    assert DBOS.retrieve_workflow(wf).get_status().status == "PENDING"
    assert "herramienta.llamada" not in [r[0] for r in pg.su.execute("SELECT accion FROM audit_log")]

    _decidir(pg, plan_id, "aprobada")
    assert _despertar(pg) == 1
    assert _resultado(wf) == "hecho"
    filas = pg.su.execute("SELECT orden, estado, salida, error FROM task ORDER BY orden").fetchall()
    assert filas[0][1] == "hecha" and [f[0] for f in filas[0][2]] == [f"H{n}" for n in range(1, 8)]
    assert filas[1][1] == "cancelada" and "lector no puede usar search_web" in filas[1][3]
    used_by = pg.su.execute("SELECT used_by FROM approval").fetchone()[0]
    assert used_by == "agent:investigador"
    rechazo = pg.su.execute("SELECT actor, datos FROM audit_log WHERE accion = 'herramienta.rechazada'").fetchone()
    assert rechazo == ("agent:lector", {"rol": "lector", "herramienta": "search_web"})
    assert auditoria.verificar(pg.su).ok


def test_plan_rechazado_cancela_todo(worker):
    pg = worker.pg
    _, wf, plan_id = _pedir_plan(pg)
    _decidir(pg, plan_id, "rechazada")
    _despertar(pg)
    assert _resultado(wf) == "rechazado"
    assert {r[0] for r in pg.su.execute("SELECT estado FROM task")} == {"cancelada"}
    assert pg.su.execute("SELECT used_at FROM approval").fetchone()[0] is None


def _simular_capturas(pg, parar: threading.Event, texto_de):
    """Hace de `capture` + `extract`: completa cada capture_job pendiente con una captura y su extracción."""
    n = 0
    while not parar.is_set():
        for jid, inv, url in pg.su.execute(
            "SELECT id, investigation_id, url FROM capture_job WHERE estado = 'pendiente'"
        ).fetchall():
            t = texto_de(url)
            enlaces = []
            if isinstance(t, tuple):
                t, enlaces = t
            if t is None:
                pg.su.execute(
                    "UPDATE capture_job SET estado = 'error', error = 'HTTP 403 al descargar' WHERE id = %s", (jid,)
                )
                continue
            n += 1
            sha = f"{n:02x}" + SHA[2:]
            cap = pg.su.execute(
                "INSERT INTO capture (created_by, investigation_id, url_final, sha256, mime, tamano, ruta, fetched_at) "
                "VALUES ('service:capture', %s, %s, %s, 'text/html', 1, %s, now()) RETURNING id",
                (inv, url, sha, f"evidencia/{sha[:2]}/{sha}"),
            ).fetchone()[0]
            ext = pg.su.execute(
                "INSERT INTO extraction (created_by, capture_id, extractor, texto, paginas, enlaces) "
                "VALUES ('service:extract', %s, 'html-1', %s, %s, %s) RETURNING id",
                (cap, t, Jsonb([{"n": 1, "inicio": 0, "fin": len(t)}]), Jsonb(enlaces)),
            ).fetchone()[0]
            pg.su.execute(
                "INSERT INTO pii_scan (created_by, extraction_id, version, nivel, texto_redactado) "
                "VALUES ('service:extract', %s, 'v', 'ninguno', %s)",
                (ext, t),
            )
            pg.su.execute("UPDATE capture_job SET estado = 'hecho', capture_id = %s WHERE id = %s", (cap, jid))
        parar.wait(0.1)


def test_descubrimiento_sigue_enlaces_oficiales_y_propone_el_canal_con_su_captura_y_cita(worker):
    """Caso real del 2026-10-29: la portada municipal enlaza «Canales de Recepción de Solicitudes»."""
    pg = worker.pg
    inv = str(_villarrica(pg.su))
    canales = "https://www.munivillarrica.cl/?page_id=1751"

    def paginas(url):
        if url == "https://www.munivillarrica.cl/":
            return "Inicio\nCanales de Recepción de Solicitudes", [
                {
                    "href": "/?page_id=1751",
                    "texto": "Canales de Recepción de Solicitudes",
                },  # relativo, como en el sitio
                {"href": "https://otro.example/x", "texto": "Solicitud de acceso"},
            ]
        if url == canales:
            return "Solicitudes de acceso a la información (Ley 20.285): transparencia@munivillarrica.cl"
        if "portaltransparencia" in url:
            return None  # el Portal responde 403
        return "Página sin correos"

    parar = threading.Event()
    hilo = threading.Thread(target=_simular_capturas, args=(pg, parar, paginas), daemon=True)
    hilo.start()
    try:
        wf = DBOS.start_workflow(flujos.flujo_descubrir, inv, "Municipalidad de Villarrica").workflow_id
        salida = _resultado(wf)
    finally:
        parar.set()
    assert canales in salida["urls"] and "https://otro.example/x" not in salida["urls"]
    k = pg.su.execute("SELECT correo, tipo, estado, cita, capture_id FROM organization_contact").fetchall()
    assert [(c, t, e) for c, t, e, _, _ in k] == [("transparencia@munivillarrica.cl", "transparencia", "candidato")]
    url_cap = pg.su.execute("SELECT url_final FROM capture WHERE id = %s", (k[0][4],)).fetchone()[0]
    assert url_cap == canales  # la evidencia es la página enlazada, no la portada
    assert any(t == "organization_contact" for t, _, _ in aprobaciones.pendientes(pg.conectar("inv_web")))


def test_inventario_marca_cada_dominio_con_evidencia(worker):
    pg = worker.pg
    inv = str(_villarrica(pg.su))
    parar = threading.Event()
    hilo = threading.Thread(
        target=_simular_capturas,
        args=(pg, parar, lambda url: None if "sinim" in url or "portaltransparencia" in url else "ok"),
        daemon=True,
    )
    hilo.start()
    try:
        _resultado(DBOS.start_workflow(flujos.flujo_inventario, inv).workflow_id)
    finally:
        parar.set()
    estados = dict(pg.su.execute("SELECT dominio, estado FROM source_domain WHERE cliente = 'capture'").fetchall())
    assert estados["www.villarrica.cl"] == "verificada"
    assert estados["www.sinim.gov.cl"] == "no_accesible"
    assert estados["www.bcn.cl"] == "inexistente"  # el resolver de prueba dice que no existe
    assert estados["www.portaltransparencia.cl"] == "no_accesible"  # el sitio rechaza al cliente (403)
    detalle = pg.su.execute("SELECT detalle FROM source_domain WHERE dominio = 'www.sinim.gov.cl'").fetchone()[0]
    assert "403" in detalle
    assert "pendiente" not in estados.values()
    sin_evidencia = pg.su.execute(
        "SELECT count(*) FROM source_domain WHERE estado = 'verificada' AND capture_id IS NULL AND cliente = 'capture'"
    ).fetchone()[0]
    assert sin_evidencia == 0
