"""Descubrimiento del canal de transparencia de un organismo, en código (especificación, «Descubrimiento de correos»).

Desviación decidida: los pasos 3 y 4 (extraer y clasificar correos) son deterministas, no del lector. Los correos
institucionales están en el texto ORIGINAL de la extracción; leerlos con código evita mandar nada a la API y deja la
cita exacta por construcción. Daniel confirma cada canal en la bandeja.

Validación de un candidato (todas quedan en `validacion`):
- formato del correo; aparece literalmente en la captura (verificador de citas);
- la página está en un dominio oficial del organismo o en el Portal de Transparencia;
- el dominio del correo es del organismo o estatal (gob.cl, gov.cl);
- el dominio del correo tiene registro MX.
Si falla el dominio del correo (p. ej. gmail.com) o el MX, el candidato queda en `revisar`; si falla algo más, no se
propone.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from investigacion import verificador

PORTAL = "www.portaltransparencia.cl"
DOMINIOS_ESTATALES = ("gob.cl", "gov.cl")
GENERICOS = ("gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "live.com", "icloud.com")
_CORREO = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
CONTEXTO = 250  # caracteres a cada lado para clasificar y para la cita

_TRANSPARENCIA = re.compile(
    r"(?i)ley\s*(?:n[°º.]?\s*)?20[.\s]?285|acceso\s+a\s+la\s+informaci[oó]n|solicitud(?:es)?\s+de\s+informaci[oó]n|"
    r"transparencia\s+pasiva"
)
_OIRS = re.compile(r"(?i)\boirs\b|oficina\s+de\s+informaciones|reclamos\s+y\s+sugerencias")


@dataclass(frozen=True)
class Candidato:
    canal: str  # correo | portal
    correo: str | None
    url_portal: str | None
    tipo: str  # transparencia | oirs | contacto_general | portal
    cita: str
    validacion: dict = field(default_factory=dict)

    @property
    def estado(self) -> str:
        v = self.validacion
        return "candidato" if all(v.get(k, True) for k in ("dominio_correo", "mx")) else "revisar"


def dominio_de(correo_o_url: str) -> str:
    if "@" in correo_o_url and "://" not in correo_o_url:
        return correo_o_url.rsplit("@", 1)[1].lower().rstrip(".")
    return (urlsplit(correo_o_url).hostname or "").lower()


def _en(dominio: str, raices) -> bool:
    return any(dominio == r or dominio.endswith("." + r) for r in raices)


def clasificar(contexto: str) -> str:
    if _TRANSPARENCIA.search(contexto):
        return "transparencia"
    if _OIRS.search(contexto):
        return "oirs"
    return "contacto_general"


def _cita(texto: str, ini: int, fin: int) -> str:
    """Fragmento literal alrededor del correo, cortado en límites de línea cuando se puede."""
    a = max(0, ini - CONTEXTO)
    b = min(len(texto), fin + CONTEXTO)
    a = texto.rfind("\n", a, ini) + 1 or a
    nl = texto.find("\n", fin, b)
    b = nl if nl != -1 else b
    return texto[a:b].strip()


def candidatos_de_texto(
    texto: str,
    url_pagina: str,
    dominios_org: list[str],
    tiene_mx: Callable[[str], bool],
    nombre_org: str = "",
) -> list[Candidato]:
    pagina = dominio_de(url_pagina)
    pagina_oficial = _en(pagina, dominios_org) or pagina == PORTAL
    if not pagina_oficial:
        return []
    salida: dict[str, Candidato] = {}
    for m in _CORREO.finditer(texto):
        correo = m.group().lower().rstrip(".")
        if correo in salida:
            continue
        cita = _cita(texto, m.start(), m.end())
        dom = dominio_de(correo)
        validacion = {
            "formato": bool(re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", correo)),
            "literal_en_captura": verificador.verificar(cita, texto, [], None).verified and m.group() in cita,
            "pagina_oficial": pagina_oficial,
            "pagina": pagina,
            "dominio_correo": (_en(dom, dominios_org) or _en(dom, DOMINIOS_ESTATALES)) and dom not in GENERICOS,
            "mx": tiene_mx(dom),
        }
        if not (validacion["formato"] and validacion["literal_en_captura"]):
            continue
        salida[correo] = Candidato("correo", correo, None, clasificar(cita), cita, validacion)
    if not salida and pagina == PORTAL and nombre_org and nombre_org.lower() in texto.lower():
        # Sin correo publicado: el canal es el formulario del Portal de Transparencia (Daniel lo ingresa a mano).
        i = texto.lower().index(nombre_org.lower())
        cita = _cita(texto, i, i + len(nombre_org))
        salida[url_pagina] = Candidato(
            "portal",
            None,
            url_pagina,
            "portal",
            cita,
            {"pagina_oficial": True, "pagina": pagina, "literal_en_captura": True},
        )
    return list(salida.values())


# --- enlaces de páginas oficiales (medido el 2026-10-29: la búsqueda web gratuita no sirve de base) -------------------

_SEGUIR = re.compile(
    r"(?i)solicitud(?:es)?\s+de\s+(?:acceso|informaci)|acceso\s+a\s+la\s+informaci|transparencia|"
    r"canales?\s+de\s+recepci|oirs|contacto|ley\s*(?:n[°º.]?\s*)?20[.\s]?285"
)
_PRIORIDAD = re.compile(r"(?i)solicitud|acceso a la informaci|canales? de recepci|pasiva|20[.\s]?285")
_SAI = re.compile(
    r"(?i)solicitud(?:es)?\s+de\s+acceso|solicitar\s+informaci|acceso\s+a\s+la\s+informaci|"
    r"transparencia\s+pasiva|solicitud\s+de\s+informaci"
)
MAX_SEGUIR = 6


def absolutos(enlaces: list[dict], url_pagina: str) -> list[dict]:
    """[{href, texto}] crudos de `extract` → [{url, texto}] absolutos, sin fragmento y solo http(s)."""
    from urllib.parse import urldefrag, urljoin

    salida, vistos = [], set()
    for e in enlaces:
        url = urldefrag(urljoin(url_pagina, e.get("href") or e.get("url", ""))).url
        if url.startswith(("https://", "http://")) and url not in vistos:
            vistos.add(url)
            salida.append({"url": url, "texto": e.get("texto", "")})
    return salida


def enlaces_a_seguir(enlaces: list[dict], dominios_org: list[str], ya: set[str]) -> list[str]:
    """Enlaces de la MISMA organización (dominio oficial) que suenan a transparencia o solicitudes, priorizados."""
    elegidos = []
    for e in enlaces:
        url, texto = e.get("url", ""), e.get("texto", "")
        if url in ya or not _en(dominio_de(url), dominios_org) or not _SEGUIR.search(f"{texto} {url}"):
            continue
        elegidos.append((0 if _PRIORIDAD.search(f"{texto} {url}") else 1, url))
    vistos: list[str] = []
    for _, u in sorted(elegidos):
        if u not in vistos:
            vistos.append(u)
    return vistos[:MAX_SEGUIR]


def candidatos_de_enlaces(texto: str, enlaces: list[dict], url_pagina: str, dominios_org: list[str]) -> list[Candidato]:
    """La página oficial enlaza el formulario de solicitudes del Portal: canal `portal`, con el ancla como cita.
    Un enlace sin id de organismo (`idOrg=undefined`) queda en `revisar`."""
    pagina = dominio_de(url_pagina)
    if not _en(pagina, dominios_org):
        return []
    salida: dict[str, Candidato] = {}
    for e in enlaces:
        url, ancla = e.get("url", ""), e.get("texto", "").strip()
        if dominio_de(url) != PORTAL or "ingreso-sai" not in url.lower() or not _SAI.search(ancla):
            continue
        if url in salida or len(ancla) < 8 or ancla not in texto:  # la cita debe estar literal en la captura
            continue
        org_id = re.search(r"(?i)idOrg=([^&]+)", url)
        con_id = bool(org_id and org_id.group(1).lower() not in ("undefined", "null", ""))
        validacion = {
            "pagina_oficial": True,
            "pagina": pagina,
            "literal_en_captura": True,
            "enlace_al_portal": True,
            "id_organismo": con_id,
            "dominio_correo": con_id,
        }  # sin id: Daniel revisa
        salida[url] = Candidato("portal", None, url, "portal", ancla, validacion)
    return list(salida.values())


ORDEN = {"transparencia": 0, "portal": 1, "oirs": 2, "contacto_general": 3}


def ordenar(candidatos: list[Candidato]) -> list[Candidato]:
    """Transparencia antes que OIRS y que contacto general; los válidos antes que los de `revisar`."""
    return sorted(candidatos, key=lambda c: (c.estado != "candidato", ORDEN[c.tipo], c.correo or c.url_portal or ""))


def tiene_mx_dns(dominio: str) -> bool:
    """Registro MX por el DNS del cluster (CoreDNS). Sin dnspython: consulta mínima con la biblioteca estándar."""
    import socket
    import struct

    def nombre(d: str) -> bytes:
        return b"".join(bytes([len(p)]) + p.encode("idna") for p in d.split(".") if p) + b"\x00"

    consulta = struct.pack(">HHHHHH", 0x1A2B, 0x0100, 1, 0, 0, 0) + nombre(dominio) + struct.pack(">HH", 15, 1)
    servidor = _resolver()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(5)
        try:
            s.sendto(consulta, (servidor, 53))
            resp, _ = s.recvfrom(4096)
        except OSError:
            return False
    return hay_mx_en_respuesta(resp, 0x1A2B)


def _saltar_nombre(m: bytes, i: int) -> int:
    while i < len(m):
        n = m[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:  # puntero de compresión
            return i + 2
        i += n + 1
    raise ValueError("nombre DNS truncado")


def hay_mx_en_respuesta(m: bytes, ident: int) -> bool:
    """¿La respuesta DNS (sin error) trae al menos un registro de tipo MX (15)?"""
    import struct

    if len(m) < 12 or struct.unpack(">H", m[:2])[0] != ident or m[3] & 0x0F:
        return False
    preguntas, respuestas = struct.unpack(">HH", m[4:8])
    try:
        i = 12
        for _ in range(preguntas):
            i = _saltar_nombre(m, i) + 4
        for _ in range(respuestas):
            i = _saltar_nombre(m, i)
            tipo, _clase, _ttl, largo = struct.unpack(">HHIH", m[i : i + 10])
            if tipo == 15:
                return True
            i += 10 + largo
    except ValueError, struct.error:
        return False
    return False


def _resolver() -> str:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as f:
            for linea in f:
                partes = linea.split()
                if len(partes) >= 2 and partes[0] == "nameserver" and "." in partes[1]:
                    return partes[1]
    except OSError:
        pass
    return "127.0.0.1"
