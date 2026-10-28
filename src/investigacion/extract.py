"""Servicio `extract`: texto + datos personales de cada captura nueva. Sin salida de red (solo Postgres y evidencia).

Por cada captura sin extracción: extrae texto por página, redacta datos personales y guarda `extraction` (original,
solo para el visor), `pii_scan` (versión redactada, mapa de desplazamientos y páginas redactadas). El nivel `alto`
(RUN de persona natural) aísla la captura: el lector no la recibe hasta que Daniel la apruebe (fase 2).
"""

from collections.abc import Callable
from pathlib import Path

import psycopg
import structlog
from psycopg.types.json import Jsonb

from investigacion import auditoria, evidencia, extraer, redaccion

log = structlog.get_logger()
ACTOR = "service:extract"
_AVISADAS: set = set()


def procesar_capturas(
    conn: psycopg.Connection,
    raiz: Path,
    detectores: tuple[redaccion.Detector, ...] = redaccion.DETECTORES_DETERMINISTAS,
    extractor: Callable[[str, bytes, str], extraer.Extraido] = extraer.extraer,
    limite: int = 20,
) -> int:
    pendientes = conn.execute(
        "SELECT c.id, c.sha256, c.mime, c.url_final FROM capture c WHERE NOT EXISTS "
        "(SELECT 1 FROM extraction e WHERE e.capture_id = c.id) ORDER BY c.created_at LIMIT %s",
        (limite,),
    ).fetchall()
    hechas = 0
    avisadas = _AVISADAS
    for cid, sha, mime, url in pendientes:
        try:
            archivo = evidencia.ruta_absoluta(raiz, sha)
            if evidencia.hash_archivo(archivo) != sha:  # no se procesa evidencia alterada
                raise evidencia.EvidenciaCorrupta(f"{archivo}: el contenido no coincide con su hash")
            e = extractor(mime, archivo.read_bytes(), url)
        except extraer.NoSoportado as err:
            # Terminal: se deja una extracción vacía para no reintentar para siempre.
            e = extraer.Extraido("", [], "no-soportado")
            log.info("sin extractor", capture=str(cid), motivo=str(err))
        except (evidencia.EvidenciaCorrupta, OSError) as err:
            if cid not in avisadas:  # una sola entrada de auditoría por captura y proceso
                avisadas.add(cid)
                auditoria.registrar(conn, ACTOR, "extract.fallo", f"capture/{cid}", {"error": str(err)[:300]})
            log.warning("extracción fallida", capture=str(cid), error=str(err))
            continue
        # Los datos tabulares (CSV de millones de caracteres) se redactan solo con los detectores deterministas: el
        # modelo de nombres corre a ~1 KB/s por CPU y tardaría horas. Queda registrado en la versión del escaneo.
        det = redaccion.DETECTORES_DETERMINISTAS if e.tabular else detectores
        version = redaccion.VERSION + ("-determinista" if det is not detectores else "")
        red = redaccion.redactar(e.texto, det)
        paginas_orig = [{"n": p.n, "inicio": p.inicio, "fin": p.fin} for p in e.paginas]
        paginas_red = [
            {
                "n": p.n,
                "inicio": redaccion.a_redactado(red.mapa, p.inicio),
                "fin": redaccion.a_redactado(red.mapa, p.fin),
            }
            for p in e.paginas
        ]
        with conn.transaction():
            eid = conn.execute(
                "INSERT INTO extraction (created_by, capture_id, extractor, idioma, texto, paginas) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (ACTOR, cid, e.extractor, e.idioma, e.texto, Jsonb(paginas_orig)),
            ).fetchone()[0]  # type: ignore[index]
            pid = conn.execute(
                "INSERT INTO pii_scan (created_by, extraction_id, version, hallazgos, nivel, texto_redactado, mapa, "
                "paginas_redactadas) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    ACTOR,
                    eid,
                    version,
                    Jsonb(red.hallazgos),
                    red.nivel,
                    red.texto,
                    Jsonb(red.mapa_json()),
                    Jsonb(paginas_red),
                ),
            ).fetchone()[0]  # type: ignore[index]
            # La auditoría registra tipos y conteos, nunca los valores.
            auditoria.registrar(
                conn,
                ACTOR,
                "extract.crear",
                f"extraction/{eid}",
                {
                    "capture": str(cid),
                    "extractor": e.extractor,
                    "paginas": len(e.paginas),
                    "pii_scan": str(pid),
                    "nivel": red.nivel,
                    "hallazgos": red.hallazgos,
                },
            )
        log.info("extraída", capture=str(cid), paginas=len(e.paginas), nivel=red.nivel)
        hechas += 1
    return hechas
