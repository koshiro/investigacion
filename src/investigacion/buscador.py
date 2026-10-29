"""Búsqueda web del investigador con el SearXNG propio (Service interno; SearXNG sale solo por el proxy).

- Devuelve SOLO título y URL: el fragmento (`content`) se descarta aquí y nunca llega a un prompt.
- Título y URL son contenido externo: viajan como `ResultadoExterno` con su procedencia y, si van a un modelo, dentro
  de un bloque `<documento>` del gateway (nunca en el prompt de sistema).
- La consulta es un canal de salida: largo máximo, sin saltos de línea y queda en la auditoría (la registra la
  herramienta `search_web`).
"""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

MAX_CONSULTA = 200
MAX_RESULTADOS = 10
MAX_TITULO = 300
MAX_URL = 2000


class BusquedaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoExterno:
    """Un resultado de búsqueda: dato no confiable, con su procedencia."""

    titulo: str
    url: str
    origen: str  # "searxng:<sha256 corto de la consulta>"

    @property
    def dominio(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()


def validar_consulta(consulta: str) -> str:
    c = re.sub(r"\s+", " ", consulta).strip()
    if not c:
        raise BusquedaInvalida("consulta vacía")
    if len(c) > MAX_CONSULTA:
        raise BusquedaInvalida(f"consulta de {len(c)} caracteres (máximo {MAX_CONSULTA})")
    return c


def origen_de(consulta: str) -> str:
    return "searxng:" + hashlib.sha256(consulta.encode()).hexdigest()[:12]


def _limpiar(texto: str, largo: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", texto).strip()[:largo]


class Buscador:
    def __init__(self, url: str, transport=None, timeout_s: float = 20.0):
        # Sin proxy ni variables de entorno: SearXNG es un Service del namespace (la salida la hace él, por el proxy).
        self._c = httpx.Client(base_url=url, timeout=timeout_s, trust_env=False, transport=transport)

    def buscar(self, consulta: str, maximo: int = MAX_RESULTADOS) -> list[ResultadoExterno]:
        c = validar_consulta(consulta)
        r = self._c.get(
            "/search",
            params={"q": c, "format": "json", "language": "es-CL", "safesearch": 1, "pageno": 1},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        origen = origen_de(c)
        salida: list[ResultadoExterno] = []
        vistos: set[str] = set()
        for item in r.json().get("results", []):
            url = str(item.get("url") or "")[:MAX_URL]
            if not url.startswith(("https://", "http://")) or url in vistos:
                continue
            vistos.add(url)
            salida.append(ResultadoExterno(_limpiar(str(item.get("title") or ""), MAX_TITULO), url, origen))
            if len(salida) >= min(maximo, MAX_RESULTADOS):
                break
        return salida


def como_documento_texto(resultados: list[ResultadoExterno]) -> str:
    """Texto para un `Documento` del gateway: una línea por resultado, marcada como externa."""
    return "\n".join(f"[resultado externo {i}] título: {r.titulo} | url: {r.url}" for i, r in enumerate(resultados, 1))
