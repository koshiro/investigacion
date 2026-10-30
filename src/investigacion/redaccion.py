"""Detección y redacción de datos personales (regla no negociable 5; Ley 21.719 vigente desde el 1-dic-2026).

Capa determinista (sin modelos, la misma en `extract` y en `llm-gateway`): RUN con dígito verificador, correos y
teléfonos de particulares. Capa de modelos (Presidio + GLiNER, solo en `extract`): nombres y direcciones; se inyecta
como un detector más. Ante la duda se redacta.

Criterio de RUT: ver `detectar_run`. Un RUT con cuerpo >= 50.000.000 es de persona jurídica y puede quedar; uno válido
menor a 30.000.000 es un RUN y se redacta; con dígito inválido o entre 30 y 50 millones solo si lo precede
«RUN/RUT/cédula».

Marcadores estables por documento: la misma persona/valor recibe siempre el mismo marcador (`[PERSONA_3]`).
`mapa` registra cada reemplazo con sus desplazamientos en el texto redactado y en el original.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

VERSION = "redaccion-3"
LIMITE_PERSONA_JURIDICA = 50_000_000
# Los RUN de personas naturales llegan hoy a ~28 millones; cuerpos de 30 a 50 millones con dígito válido son casi
# siempre
# códigos (p. ej. el código BIP «30403927-0», que usa el mismo módulo 11). Solo se tratan como RUN con contexto.
LIMITE_RUN_SIN_CONTEXTO = 30_000_000
DOMINIOS_INSTITUCIONALES = ("gob.cl", "gov.cl", "minvu.cl", "mop.cl", "villarrica.cl", "portaltransparencia.cl")


@dataclass(frozen=True)
class Hallazgo:
    inicio: int
    fin: int
    tipo: str  # RUN | CORREO | TELEFONO | PERSONA | DIRECCION
    valor: str


@dataclass(frozen=True)
class Reemplazo:
    red_inicio: int
    red_fin: int
    orig_inicio: int
    orig_fin: int
    tipo: str
    marcador: str


@dataclass
class Redaccion:
    texto: str
    mapa: list[Reemplazo]
    hallazgos: dict[str, int] = field(default_factory=dict)

    @property
    def nivel(self) -> str:
        """`alto` si hay un RUN de persona natural (aísla la captura); `bajo` si solo hay otros datos."""
        if self.hallazgos.get("RUN", 0):
            return "alto"
        return "bajo" if self.hallazgos else "ninguno"

    def mapa_json(self) -> list[dict]:
        return [asdict(m) for m in self.mapa]


Detector = Callable[[str], list[Hallazgo]]

# --- RUN / RUT ---------------------------------------------------------------------------------------------------

# Formato real de un RUT: puntos de miles o corrido, y guion PEGADO al dígito verificador. Sin espacios: «55.338.159 -
# 1.813.694» (resta en una tabla) y «137.107 -0» no son RUT. Medido con el CSV del BIP y una tabla financiera real.
_RUT = re.compile(r"(?<![\w.,-])(\d{1,2}(?:\.\d{3}){2}|\d{7,8})-([\dkK])(?![\w-])")
_CONTEXTO_RUT = re.compile(r"(?i)\b(?:run|rut|c[eé]dula|c\.?\s?i\.?|identidad)\W{0,12}$")


def digito_verificador(cuerpo: int) -> str:
    suma, factor = 0, 2
    for d in reversed(str(cuerpo)):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - suma % 11
    return {11: "0", 10: "K"}.get(resto, str(resto))


def detectar_run(texto: str) -> list[Hallazgo]:
    """RUN de persona natural. Criterio (con evidencia en docs/FASE-2.md):
    - dígito verificador válido y cuerpo < 30 M → RUN (se redacta);
    - cuerpo ≥ 50 M válido → persona jurídica, puede quedar;
    - cuerpo entre 30 y 50 M válido, o dígito inválido → solo es RUN si lo precede «RUN», «RUT», «cédula»…
    """
    salida = []
    for m in _RUT.finditer(texto):
        cuerpo = int(re.sub(r"\D", "", m.group(1)))
        dv = m.group(2).upper()
        valido = digito_verificador(cuerpo) == dv
        contexto = bool(_CONTEXTO_RUT.search(texto[max(0, m.start() - 24) : m.start()]))
        if valido and cuerpo >= LIMITE_PERSONA_JURIDICA:
            continue  # empresa u órgano público: puede quedar
        if not contexto and (not valido or cuerpo >= LIMITE_RUN_SIN_CONTEXTO):
            continue  # un número cualquiera o un código, no un RUN
        salida.append(Hallazgo(m.start(), m.end(), "RUN", f"{cuerpo}-{dv}"))
    return salida


# --- correo y teléfono -------------------------------------------------------------------------------------------

_CORREO = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# Un teléfono no va pegado a letras ni a guiones: «9d6c69d9-8914-4559-…» (un UUID) daba «9 8914 4559» en el 0,7 % de
# los UUID y el gateway bloqueaba la llamada por «dato personal residual» (medido el 2026-10-30; redaccion-3).
_CELULAR = re.compile(r"(?<![\w.,-])(?:\+?56[\s-]?)?9[\s-]?\d{4}[\s-]?\d{4}(?![\w-]|[.,]\d)")
_FIJO_CON_PREFIJO = re.compile(r"(?<![\w.,-])\+56[\s-]?[2-8]\d?[\s-]?\d{3,4}[\s-]?\d{4}(?![\w-]|[.,]\d)")
_FIJO_CON_CONTEXTO = re.compile(
    r"(?i:fono|tel[eé]fono|tel\.|celular|fax|contacto)\W{0,12}((?:\(?\d{2}\)?[\s-]?)?\d{3,4}[\s-]?\d{4})(?![\d]|[.,]\d)"
)


def detectar_correo(texto: str, institucionales: tuple[str, ...] = DOMINIOS_INSTITUCIONALES) -> list[Hallazgo]:
    salida = []
    for m in _CORREO.finditer(texto):
        dominio = m.group(1).lower()
        if any(dominio == d or dominio.endswith("." + d) for d in institucionales):
            continue  # correo de un organismo: no es dato de un particular
        salida.append(Hallazgo(m.start(), m.end(), "CORREO", m.group().lower()))
    return salida


def detectar_telefono(texto: str) -> list[Hallazgo]:
    salida = [Hallazgo(m.start(), m.end(), "TELEFONO", re.sub(r"\D", "", m.group())) for m in _CELULAR.finditer(texto)]
    salida += [
        Hallazgo(m.start(), m.end(), "TELEFONO", re.sub(r"\D", "", m.group()))
        for m in _FIJO_CON_PREFIJO.finditer(texto)
    ]
    salida += [
        Hallazgo(m.start(1), m.end(1), "TELEFONO", re.sub(r"\D", "", m.group(1)))
        for m in _FIJO_CON_CONTEXTO.finditer(texto)
    ]
    return salida


DETECTORES_DETERMINISTAS: tuple[Detector, ...] = (detectar_run, detectar_correo, detectar_telefono)


# --- redacción ---------------------------------------------------------------------------------------------------


def _clave(h: Hallazgo) -> str:
    valor = unicodedata.normalize("NFKC", h.valor).casefold()
    return re.sub(r"\s+", " ", valor).strip()


def _sin_solapes(hallazgos: list[Hallazgo]) -> list[Hallazgo]:
    """Ante solapes gana el más largo; entre iguales, el que empieza antes."""
    elegidos: list[Hallazgo] = []
    for h in sorted(hallazgos, key=lambda h: (-(h.fin - h.inicio), h.inicio)):
        if all(h.fin <= e.inicio or h.inicio >= e.fin for e in elegidos):
            elegidos.append(h)
    return sorted(elegidos, key=lambda h: h.inicio)


def redactar(
    texto: str,
    detectores: tuple[Detector, ...] = DETECTORES_DETERMINISTAS,
    registro: dict[tuple[str, str], str] | None = None,
) -> Redaccion:
    """Reemplaza cada hallazgo por un marcador estable. `registro` conserva marcadores entre documentos."""
    registro = registro if registro is not None else {}
    todos = [h for d in detectores for h in d(texto)]
    salida: list[str] = []
    mapa: list[Reemplazo] = []
    cuentas: dict[str, int] = {}
    contadores: dict[str, int] = {}
    for (tipo, _), marcador in registro.items():
        contadores[tipo] = max(contadores.get(tipo, 0), int(marcador[:-1].rsplit("_", 1)[1]))
    cursor = 0
    largo_red = 0
    for h in _sin_solapes(todos):
        clave = (h.tipo, _clave(h))
        if clave not in registro:
            contadores[h.tipo] = contadores.get(h.tipo, 0) + 1
            registro[clave] = f"[{h.tipo}_{contadores[h.tipo]}]"
        marcador = registro[clave]
        salida.append(texto[cursor : h.inicio])
        largo_red += h.inicio - cursor
        mapa.append(Reemplazo(largo_red, largo_red + len(marcador), h.inicio, h.fin, h.tipo, marcador))
        salida.append(marcador)
        largo_red += len(marcador)
        cursor = h.fin
        cuentas[h.tipo] = cuentas.get(h.tipo, 0) + 1
    salida.append(texto[cursor:])
    return Redaccion("".join(salida), mapa, cuentas)


def residuos(texto: str, detectores: tuple[Detector, ...] = DETECTORES_DETERMINISTAS) -> list[Hallazgo]:
    """Datos personales que sobreviven en un texto ya redactado; el gateway rechaza el envío si no está vacío."""
    return [h for d in detectores for h in d(texto)]


# --- traducción de posiciones ------------------------------------------------------------------------------------


def a_original(mapa: list[Reemplazo], inicio: int, fin: int) -> tuple[int, int]:
    """Traduce un rango del texto redactado al original. Si toca un marcador, se expande al dato completo."""

    def convertir(p: int, hacia_el_final: bool) -> int:
        delta = 0
        for r in mapa:
            if r.red_fin <= p:
                delta += (r.orig_fin - r.orig_inicio) - (r.red_fin - r.red_inicio)
            elif r.red_inicio < p:  # dentro de un marcador
                return r.orig_fin if hacia_el_final else r.orig_inicio
            else:
                break
        return p + delta

    return convertir(inicio, False), convertir(fin, True)


def a_redactado(mapa: list[Reemplazo], pos: int) -> int:
    """Posición del texto original → posición equivalente en el redactado (para recolocar los límites de página)."""
    delta = 0
    for r in mapa:
        if r.orig_fin <= pos:
            delta += (r.red_fin - r.red_inicio) - (r.orig_fin - r.orig_inicio)
        elif r.orig_inicio < pos:
            return r.red_inicio
        else:
            break
    return pos + delta
