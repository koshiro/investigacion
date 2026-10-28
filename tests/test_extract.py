"""Extracción y redacción: texto por página, desplazamientos, nivel de datos personales, aislamiento y sin red."""

import hashlib
import inspect
import json

import pytest

from investigacion import auditoria, evidencia, extract, extraer, redaccion, verificador


def test_csv_con_punto_y_coma_y_latin1():
    datos = "Código BIP;Nombre;Monto\n30481234-0;Mejoramiento calle;245.800.000\n".encode("cp1252")
    e = extraer.extraer("text/csv", datos)
    assert "Código BIP | Nombre | Monto" in e.texto and "30481234-0 | Mejoramiento calle | 245.800.000" in e.texto
    assert len(e.paginas) == 1 and e.paginas[0].inicio == 0 and e.paginas[0].fin == len(e.texto)


def test_csv_octet_stream_se_reconoce_por_la_url():
    e = extraer.extraer("application/octet-stream", b"a,b\n1,2\n", "https://x.cl/datos.csv?v=1")
    assert "a | b" in e.texto


def test_paginas_artificiales_cada_200_lineas_con_desplazamientos_exactos():
    e = extraer.extraer_texto(("linea\n" * 450).encode())
    assert [p.n for p in e.paginas] == [1, 2, 3]
    for p in e.paginas:
        assert e.texto[p.inicio : p.fin].count("linea") == (200 if p.n < 3 else 50)


def test_html_quita_scripts_y_conserva_texto():
    html = b"<html><script>var x=1</script><body><h1>Transparencia</h1><p>Correo: transparencia@villarrica.cl</p></body></html>"
    t = extraer.extraer("text/html", html).texto
    assert "var x" not in t and "Transparencia" in t and "transparencia@villarrica.cl" in t


def test_formato_desconocido_no_se_inventa():
    with pytest.raises(extraer.NoSoportado):
        extraer.extraer("image/png", b"\x89PNG")


def _captura(pg, tmp_path, contenido: bytes, mime="text/plain", url="https://x.cl/a.txt"):
    inv = pg.su.execute(
        "INSERT INTO investigation (created_by, titulo, pregunta) VALUES ('d','t','p') RETURNING id"
    ).fetchone()[0]
    g = evidencia.guardar_bytes(tmp_path, contenido)
    return pg.su.execute(
        "INSERT INTO capture (created_by, investigation_id, url_final, sha256, mime, tamano, ruta, fetched_at) "
        "VALUES ('service:capture', %s, %s, %s, %s, %s, %s, now()) RETURNING id",
        (inv, url, g.sha256, mime, g.tamano, g.ruta),
    ).fetchone()[0]


TEXTO = "Acta de recepción.\nContacto del vecino: pedro@gmail.com, RUN 12.345.678-5.\nMonto aprobado $ 245.800.000.\n"


def test_extract_guarda_original_redactado_mapa_y_audita_sin_valores(pg, tmp_path):
    cid = _captura(pg, tmp_path, TEXTO.encode())
    n = extract.procesar_capturas(pg.conectar("inv_extract"), tmp_path)
    assert n == 1
    texto, nivel, red, mapa, pags = pg.su.execute(
        "SELECT e.texto, p.nivel, p.texto_redactado, p.mapa, p.paginas_redactadas FROM extraction e "
        "JOIN pii_scan p ON p.extraction_id = e.id WHERE e.capture_id = %s",
        (cid,),
    ).fetchone()
    assert texto == TEXTO.strip() and nivel == "alto"
    assert "12.345.678" not in red and "pedro@gmail.com" not in red and "$ 245.800.000" in red
    assert len(mapa) == 2 and pags[0]["inicio"] == 0 and pags[0]["fin"] == len(red)
    # La auditoría guarda tipos y conteos, jamás los datos personales.
    registro = json.dumps(pg.su.execute("SELECT datos FROM audit_log WHERE accion = 'extract.crear'").fetchone()[0])
    assert "12.345.678" not in registro and "pedro" not in registro and '"RUN": 1' in registro
    assert auditoria.verificar(pg.su).ok


def test_no_reprocesa_capturas_ya_extraidas(pg, tmp_path):
    _captura(pg, tmp_path, TEXTO.encode())
    c = pg.conectar("inv_extract")
    assert extract.procesar_capturas(c, tmp_path) == 1 and extract.procesar_capturas(c, tmp_path) == 0


def test_cita_se_verifica_contra_la_version_redactada_y_se_traduce_al_original(pg, tmp_path):
    cid = _captura(pg, tmp_path, TEXTO.encode())
    extract.procesar_capturas(pg.conectar("inv_extract"), tmp_path)
    original, red, mapa, pags = pg.su.execute(
        "SELECT e.texto, p.texto_redactado, p.mapa, p.paginas_redactadas FROM extraction e "
        "JOIN pii_scan p ON p.extraction_id = e.id WHERE e.capture_id = %s",
        (cid,),
    ).fetchone()
    paginas = [verificador.Pagina(p["n"], p["inicio"], p["fin"]) for p in pags]
    r = verificador.verificar("Contacto del vecino: [CORREO_1], RUN [RUN_1].", red, paginas, 1)
    assert r.verified
    mapa_obj = [redaccion.Reemplazo(**m) for m in mapa]
    ini, fin = redaccion.a_original(mapa_obj, r.inicio, r.fin)
    assert "pedro@gmail.com" in original[ini:fin]  # el visor puede mostrar la cita en el original


def test_evidencia_alterada_no_se_procesa_y_se_audita_una_vez(pg, tmp_path):
    cid = _captura(pg, tmp_path, TEXTO.encode())
    sha = pg.su.execute("SELECT sha256 FROM capture WHERE id = %s", (cid,)).fetchone()[0]
    archivo = evidencia.ruta_absoluta(tmp_path, sha)
    archivo.chmod(0o644)
    archivo.write_bytes(b"manipulado")
    c = pg.conectar("inv_extract")
    extract._AVISADAS.clear()
    assert extract.procesar_capturas(c, tmp_path) == 0 and extract.procesar_capturas(c, tmp_path) == 0
    assert pg.su.execute("SELECT count(*) FROM extraction").fetchone()[0] == 0
    assert pg.su.execute("SELECT count(*) FROM audit_log WHERE accion = 'extract.fallo'").fetchone()[0] == 1


def test_formato_no_soportado_deja_extraccion_vacia_y_terminal(pg, tmp_path):
    _captura(pg, tmp_path, b"\x89PNG\r\n", mime="image/png", url="https://x.cl/f.png")
    c = pg.conectar("inv_extract")
    assert extract.procesar_capturas(c, tmp_path) == 1 and extract.procesar_capturas(c, tmp_path) == 0
    assert pg.su.execute("SELECT extractor FROM extraction").fetchone()[0] == "no-soportado"


def test_extract_no_importa_librerias_de_red():
    """Defensa en profundidad: además de la NetworkPolicy, el servicio ni siquiera carga clientes HTTP."""
    fuente = inspect.getsource(extract) + inspect.getsource(extraer)
    for prohibido in ("httpx", "requests", "urllib", "socket", "aiohttp", "smtplib"):
        assert f"import {prohibido}" not in fuente and f"from {prohibido}" not in fuente


def test_hash_del_archivo_no_cambia_al_extraer(pg, tmp_path):
    cid = _captura(pg, tmp_path, TEXTO.encode())
    extract.procesar_capturas(pg.conectar("inv_extract"), tmp_path)
    sha = pg.su.execute("SELECT sha256 FROM capture WHERE id = %s", (cid,)).fetchone()[0]
    assert hashlib.sha256((tmp_path / sha[:2] / sha).read_bytes()).hexdigest() == sha


def test_csv_se_redacta_solo_con_detectores_deterministas_y_queda_registrado(pg, tmp_path):
    llamadas = []

    def nombres(texto):
        llamadas.append(len(texto))
        return []

    _captura(pg, tmp_path, b"a;b\npedro@gmail.com;1\n", mime="text/csv", url="https://x.cl/d.csv")
    _captura(pg, tmp_path, b"Texto libre del acta sin tablas.", mime="text/plain", url="https://x.cl/a.txt")
    extract.procesar_capturas(pg.conectar("inv_extract"), tmp_path, (*redaccion.DETECTORES_DETERMINISTAS, nombres))
    versiones = dict(
        pg.su.execute(
            "SELECT c.mime, p.version FROM pii_scan p JOIN extraction e ON e.id = p.extraction_id JOIN capture c ON c.id = e.capture_id"
        ).fetchall()
    )
    assert versiones == {"text/csv": f"{redaccion.VERSION}-determinista", "text/plain": redaccion.VERSION}
    assert len(llamadas) == 1  # el detector de nombres solo vio el texto libre
    # Lo determinista sigue aplicando: el correo del CSV se redactó.
    assert (
        "pedro@gmail.com"
        not in pg.su.execute(
            "SELECT texto_redactado FROM pii_scan p JOIN extraction e ON e.id = p.extraction_id JOIN capture c ON c.id = e.capture_id WHERE c.mime = 'text/csv'"
        ).fetchone()[0]
    )
