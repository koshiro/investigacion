"""Servicio `capture`: descarga por el proxy con lista blanca, guarda en evidencia y registra la captura.

Regla 6: no hay salida directa. El cliente httpx exige la URL del proxy (con el usuario `capture`) y no lee variables
de entorno de proxy; sin ella no se construye. Regla 3: el almacén no sobrescribe. Regla 7: toda captura queda auditada.
"""

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import structlog
from psycopg.types.json import Jsonb

from investigacion import auditoria, evidencia

log = structlog.get_logger()
ACTOR = "service:capture"
CABECERAS_UTILES = ("content-type", "content-length", "last-modified", "etag", "date", "content-disposition")


class CapturaError(Exception):
    pass


@dataclass(frozen=True)
class Descarga:
    guardado: evidencia.Guardado
    url_final: str
    mime: str
    estado_http: int
    cabeceras: dict[str, str]
    fetched_at: datetime


def mime_de(cabeceras: Mapping[str, str], primeros_bytes: bytes) -> str:
    declarado = cabeceras.get("content-type", "").split(";")[0].strip().lower()
    if primeros_bytes.startswith(b"%PDF"):
        return "application/pdf"
    return declarado or "application/octet-stream"


class Descargador:
    def __init__(self, proxy_url: str, max_bytes: int = 100 * 1024 * 1024, timeout_s: float = 120.0, transport=None):
        if not proxy_url:
            raise ValueError("capture exige proxy_url: no hay salida directa a internet")
        # `transport` solo se inyecta en pruebas; en producción todo va por el proxy.
        self._cliente = httpx.Client(
            proxy=None if transport else proxy_url,
            transport=transport,
            trust_env=False,
            follow_redirects=True,
            max_redirects=5,
            timeout=httpx.Timeout(timeout_s, connect=20.0),
            headers={
                "User-Agent": "investigacion-capture/1 (+evidencia publica; contacto: contacto@danielriquelme.cl)"
            },
        )
        self.max_bytes = max_bytes

    def descargar(self, url: str, raiz: Path) -> Descarga:
        if not url.lower().startswith(("http://", "https://")):
            raise CapturaError(f"esquema no permitido: {url}")
        try:
            with self._cliente.stream("GET", url) as r:
                if not r.is_success:
                    raise CapturaError(f"HTTP {r.status_code} al descargar {url}")
                escritor = evidencia.Escritor(raiz, self.max_bytes)
                primeros = b""
                try:
                    for bloque in r.iter_bytes(64 * 1024):
                        if not primeros:
                            primeros = bloque[:8]
                        escritor.escribir(bloque)
                except BaseException:
                    with contextlib.suppress(ValueError, OSError):
                        escritor.descartar()
                    raise
                cabeceras = {k: v for k, v in r.headers.items() if k.lower() in CABECERAS_UTILES}
                return Descarga(
                    guardado=escritor.publicar(),
                    url_final=str(r.url),
                    mime=mime_de(r.headers, primeros),
                    estado_http=r.status_code,
                    cabeceras=cabeceras,
                    fetched_at=datetime.now(UTC),
                )
        except httpx.HTTPError as e:
            raise CapturaError(f"{type(e).__name__} al descargar {url}: {e}") from e
        except ValueError as e:
            raise CapturaError(str(e)) from e


def registrar(
    conn: psycopg.Connection, d: Descarga, investigation_id: str, source_id: str | None = None, actor: str = ACTOR
) -> str:
    """Inserta la fila `capture` y la entrada de auditoría en la misma transacción."""
    with conn.transaction():
        capture_id = conn.execute(
            "INSERT INTO capture (created_by, source_id, investigation_id, url_final, sha256, mime, tamano, ruta, "
            "estado_http, fetched_at, cabeceras) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                actor,
                source_id,
                investigation_id,
                d.url_final,
                d.guardado.sha256,
                d.mime,
                d.guardado.tamano,
                d.guardado.ruta,
                d.estado_http,
                d.fetched_at,
                Jsonb(d.cabeceras),
            ),
        ).fetchone()[0]  # type: ignore[index]
        auditoria.registrar(
            conn,
            actor,
            "capture.crear",
            f"capture/{capture_id}",
            {"sha256": d.guardado.sha256, "url": d.url_final, "tamano": d.guardado.tamano, "nuevo": d.guardado.nuevo},
        )
    return str(capture_id)


def procesar_pendientes(conn: psycopg.Connection, descargador: Descargador, raiz: Path, limite: int = 10) -> int:
    """Toma trabajos de `capture_job` (SKIP LOCKED), descarga y registra. Devuelve cuántos procesó."""
    hechos = 0
    for _ in range(limite):
        with conn.transaction():
            fila = conn.execute(
                "SELECT id, investigation_id, source_id, url FROM capture_job WHERE estado = 'pendiente' "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ).fetchone()
            if fila is None:
                break
            job_id, inv, src, url = fila
            conn.execute("UPDATE capture_job SET estado='en_curso', actualizado_at=now() WHERE id=%s", (job_id,))
        try:
            d = descargador.descargar(url, raiz)
            capture_id = registrar(conn, d, str(inv), str(src) if src else None)
            conn.execute(
                "UPDATE capture_job SET estado='hecho', capture_id=%s, actualizado_at=now() WHERE id=%s",
                (capture_id, job_id),
            )
            log.info("captura", url=url, sha256=d.guardado.sha256, nuevo=d.guardado.nuevo)
        except (CapturaError, evidencia.EvidenciaCorrupta) as e:
            conn.execute(
                "UPDATE capture_job SET estado='error', error=%s, actualizado_at=now() WHERE id=%s",
                (str(e)[:500], job_id),
            )
            auditoria.registrar(
                conn, ACTOR, "capture.fallo", f"capture_job/{job_id}", {"url": url, "error": str(e)[:500]}
            )
            log.warning("captura fallida", url=url, error=str(e))
        hechos += 1
    return hechos


@dataclass(frozen=True)
class Discrepancia:
    capture_id: str
    motivo: str


def verificar_capturas(conn: psycopg.Connection, raiz: Path) -> list[Discrepancia]:
    """Recalcula el SHA-256 de cada archivo y lo compara con la fila y con la auditoría. Vacío = todo íntegro."""
    auditados = {
        r[0]: r[1]
        for r in conn.execute("SELECT objeto, datos->>'sha256' FROM audit_log WHERE accion = 'capture.crear'")
    }
    problemas = []
    for cid, sha, ruta in conn.execute("SELECT id, sha256, ruta FROM capture ORDER BY created_at").fetchall():
        archivo = raiz / Path(ruta).relative_to("evidencia")
        if auditados.get(f"capture/{cid}") != sha:
            problemas.append(Discrepancia(str(cid), "el hash de la fila no coincide con el registrado en la auditoría"))
        if not archivo.exists():
            problemas.append(Discrepancia(str(cid), "falta el archivo"))
        elif evidencia.hash_archivo(archivo) != sha:
            problemas.append(Discrepancia(str(cid), "el contenido del archivo no coincide con su hash"))
    return problemas
