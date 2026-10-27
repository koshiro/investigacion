"""Captura: hash, no sobrescribir, solo por proxy y detección de alteraciones (reglas 3, 6 y 7)."""

import hashlib
import stat

import httpx
import pytest

from investigacion import auditoria, capture, evidencia

PDF = b"%PDF-1.7\n" + b"contenido de prueba\n" * 50


def _cliente(handler, tmp_path):
    return capture.Descargador("http://capture:x@proxy:3128", transport=httpx.MockTransport(handler))


def test_guarda_direccionado_por_hash_y_de_solo_lectura(tmp_path):
    g = evidencia.guardar_bytes(tmp_path, PDF)
    sha = hashlib.sha256(PDF).hexdigest()
    assert g.sha256 == sha and g.ruta == f"evidencia/{sha[:2]}/{sha}" and g.nuevo
    archivo = tmp_path / sha[:2] / sha
    assert archivo.read_bytes() == PDF
    assert stat.S_IMODE(archivo.stat().st_mode) == 0o444
    assert [p.name for p in tmp_path.iterdir()] == [sha[:2]]  # sin temporales sueltos


def test_mismo_contenido_se_guarda_una_vez(tmp_path):
    a, b = evidencia.guardar_bytes(tmp_path, PDF), evidencia.guardar_bytes(tmp_path, PDF)
    assert a.nuevo and not b.nuevo and a.sha256 == b.sha256


def test_no_sobrescribe_un_archivo_existente_con_otro_contenido(tmp_path):
    sha = hashlib.sha256(PDF).hexdigest()
    destino = tmp_path / sha[:2] / sha
    destino.parent.mkdir(parents=True)
    destino.write_bytes(b"alterado")
    with pytest.raises(evidencia.EvidenciaCorrupta):
        evidencia.guardar_bytes(tmp_path, PDF)
    assert destino.read_bytes() == b"alterado"  # no se pisó
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_limite_de_tamano_aborta_sin_dejar_residuos(tmp_path):
    with pytest.raises(ValueError, match="límite"):
        evidencia.guardar_bytes(tmp_path, b"x" * 100, max_bytes=10)
    assert list(tmp_path.iterdir()) == []


def test_sin_proxy_no_se_construye_el_cliente():
    with pytest.raises(ValueError, match="proxy"):
        capture.Descargador("")


def test_descarga_pdf_con_mime_y_cabeceras(tmp_path):
    def handler(req):
        return httpx.Response(
            200, content=PDF, headers={"content-type": "application/octet-stream", "set-cookie": "a=b"}
        )

    d = _cliente(handler, tmp_path).descargar("https://www.villarrica.cl/doc.pdf", tmp_path)
    assert d.mime == "application/pdf" and d.estado_http == 200
    assert "set-cookie" not in d.cabeceras  # no se guardan cookies


def test_error_http_y_esquema_invalido_fallan_cerrado(tmp_path):
    c = _cliente(lambda req: httpx.Response(403), tmp_path)
    with pytest.raises(capture.CapturaError, match="403"):
        c.descargar("https://www.portaltransparencia.cl/x", tmp_path)
    with pytest.raises(capture.CapturaError, match="esquema"):
        c.descargar("file:///etc/passwd", tmp_path)
    with pytest.raises(capture.CapturaError, match="esquema"):
        c.descargar("ftp://servidor/archivo", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_descarga_demasiado_grande_se_rechaza(tmp_path):
    c = capture.Descargador(
        "http://p:3128", max_bytes=100, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 500))
    )
    with pytest.raises(capture.CapturaError, match="límite"):
        c.descargar("https://www.villarrica.cl/grande", tmp_path)
    assert list(tmp_path.iterdir()) == []


def _sembrar(pg):
    inv = pg.su.execute(
        "INSERT INTO investigation (created_by, titulo, pregunta) VALUES ('daniel','t','p') RETURNING id"
    ).fetchone()[0]
    return str(inv)


def _capturar(pg, tmp_path, contenido=PDF, url="https://www.villarrica.cl/a.pdf"):
    inv = _sembrar(pg)
    c = _cliente(
        lambda req: httpx.Response(200, content=contenido, headers={"content-type": "application/pdf"}), tmp_path
    )
    d = c.descargar(url, tmp_path)
    return capture.registrar(pg.conectar("inv_capture"), d, inv), d


def test_registrar_deja_captura_y_auditoria_y_todo_verifica(pg, tmp_path):
    cid, d = _capturar(pg, tmp_path)
    fila = pg.su.execute("SELECT sha256, ruta, mime FROM capture WHERE id = %s", (cid,)).fetchone()
    assert fila == (d.guardado.sha256, d.guardado.ruta, "application/pdf")
    assert auditoria.verificar(pg.su).filas == 1
    assert capture.verificar_capturas(pg.su, tmp_path) == []


def test_hash_recalculado_coincide_con_el_registrado(pg, tmp_path):
    cid, _ = _capturar(pg, tmp_path)
    sha, ruta = pg.su.execute("SELECT sha256, ruta FROM capture WHERE id = %s", (cid,)).fetchone()
    assert evidencia.hash_archivo(tmp_path / ruta.removeprefix("evidencia/")) == sha


def test_modificar_el_archivo_de_una_captura_se_detecta(pg, tmp_path):
    cid, d = _capturar(pg, tmp_path)
    archivo = evidencia.ruta_absoluta(tmp_path, d.guardado.sha256)
    archivo.chmod(0o644)
    archivo.write_bytes(PDF + b"manipulado")
    problemas = capture.verificar_capturas(pg.su, tmp_path)
    assert [p.capture_id for p in problemas] == [cid] and "contenido" in problemas[0].motivo


def test_borrar_el_archivo_se_detecta(pg, tmp_path):
    _, d = _capturar(pg, tmp_path)
    evidencia.ruta_absoluta(tmp_path, d.guardado.sha256).unlink()
    assert "falta" in capture.verificar_capturas(pg.su, tmp_path)[0].motivo


def test_cambiar_el_hash_en_la_base_se_detecta_con_la_auditoria(pg, tmp_path):
    cid, _ = _capturar(pg, tmp_path)
    otro = "ab" + "0" * 62
    pg.su.execute("ALTER TABLE capture DISABLE TRIGGER capture_inmutable")
    pg.su.execute("ALTER TABLE capture DROP CONSTRAINT capture_check")
    pg.su.execute("UPDATE capture SET sha256 = %s, ruta = %s WHERE id = %s", (otro, f"evidencia/ab/{otro}", cid))
    motivos = " ".join(p.motivo for p in capture.verificar_capturas(pg.su, tmp_path))
    assert "auditoría" in motivos


def test_cola_de_capturas_procesa_y_marca_errores(pg, tmp_path):
    inv = _sembrar(pg)
    for url in ("https://www.villarrica.cl/ok.pdf", "https://www.contraloria.cl/no.pdf"):
        pg.su.execute(
            "INSERT INTO capture_job (created_by, investigation_id, url) VALUES ('daniel', %s, %s)", (inv, url)
        )

    def handler(req):
        return httpx.Response(200, content=PDF) if "villarrica" in str(req.url) else httpx.Response(404)

    n = capture.procesar_pendientes(pg.conectar("inv_capture"), _cliente(handler, tmp_path), tmp_path)
    assert n == 2
    estados = dict(pg.su.execute("SELECT url, estado FROM capture_job").fetchall())
    assert estados == {"https://www.villarrica.cl/ok.pdf": "hecho", "https://www.contraloria.cl/no.pdf": "error"}
    assert pg.su.execute("SELECT count(*) FROM audit_log WHERE accion = 'capture.fallo'").fetchone()[0] == 1
    assert auditoria.verificar(pg.su).ok
