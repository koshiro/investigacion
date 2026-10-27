"""Verificador determinista de citas (regla no negociable 4). Ningún LLM interviene aquí.

Contra qué texto: la cita se verifica contra la extracción **redactada** (la que vio el lector), porque el lector
nunca ve el original. Las posiciones devueltas están en coordenadas de ese texto redactado; `redaccion.a_original`
las traduce al documento original para mostrarlas en el visor.

Pasos (docs/especificacion-mvp.md, «Verificación de citas»):
1. Normalizar cita y texto: Unicode NFKC, comillas y guiones tipográficos, espacios y guiones de corte de línea.
2. Coincidencia exacta en la página indicada; si no, en todo el documento.
3. Exacta → `verificada`. Aproximada (similitud ≥ 0,97) → `revisar` (verified = false, decide Daniel). Si no, `fallida`.
4. Cifras: los números de la cita deben ser los mismos que los del texto citado (formato chileno de miles y decimales);
   una diferencia numérica nunca es «aproximada»: es `fallida`.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

VERSION = "verificador-1"
UMBRAL_APROXIMADO = 97.0  # similitud mínima (0-100) para «revisar»
LARGO_MINIMO = 12  # una cita más corta que esto (ya normalizada) no prueba nada

# Comillas y guiones tipográficos → ASCII (código Unicode explícito: los caracteres literales son ambiguos a la vista).
_TIPOGRAFICOS = {
    **dict.fromkeys(map(ord, "\u201c\u201d\u201e\u00ab\u00bb"), '"'),
    **dict.fromkeys(map(ord, "\u2018\u2019"), "'"),
    **dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-"),
}
_GUION_DE_CORTE = re.compile(r"(?<=[^\W\d_])[-\u00ad][ \t\r]*\n[ \t\r]*(?=[^\W\d_])")
_NUMERO = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Pagina:
    n: int
    inicio: int  # desplazamiento dentro del texto completo
    fin: int


@dataclass(frozen=True)
class Resultado:
    estado: str  # verificada | revisar | fallida
    verified: bool
    similitud: float
    inicio: int | None = None  # coordenadas del texto completo (redactado)
    fin: int | None = None
    pagina: int | None = None
    motivo: str = ""


def _grupos(texto: str):
    """Agrupa cada carácter base con sus marcas combinantes para que NFKC componga igual que en la fuente."""
    inicio = 0
    for i in range(1, len(texto) + 1):
        if i == len(texto) or not unicodedata.category(texto[i]).startswith("M"):
            yield inicio, texto[inicio:i]
            inicio = i


def normalizar(texto: str) -> tuple[str, list[int]]:
    """Devuelve el texto normalizado y, por cada carácter, su posición en `texto` (para volver al original)."""
    # Un guion de corte (letra, '-', salto de línea, letra) se elimina completo; sus posiciones se descartan.
    descartar: set[int] = set()
    for m in _GUION_DE_CORTE.finditer(texto):
        descartar.update(range(m.start(), m.end()))
    salida: list[str] = []
    mapa: list[int] = []
    for pos, grupo in _grupos(texto):
        if pos in descartar:
            continue
        for ch in unicodedata.normalize("NFKC", grupo).translate(_TIPOGRAFICOS):
            if ch == "­":  # guion blando
                continue
            if ch.isspace():
                if not salida or salida[-1] == " ":
                    continue
                ch = " "
            salida.append(ch)
            mapa.append(pos)
    while salida and salida[-1] == " ":
        salida.pop()
        mapa.pop()
    return "".join(salida), mapa


def numero(texto: str) -> Decimal | None:
    """Número en formato chileno: «1.234.567,89» → 1234567.89; «3,5» → 3.5; «1.234» → 1234 (punto = miles)."""
    t = texto.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def cifras(texto: str) -> Counter[Decimal]:
    c: Counter[Decimal] = Counter()
    for m in _NUMERO.finditer(texto):
        n = numero(m.group())
        if n is not None:
            c[n.normalize()] += 1
    return c


def _pagina_de(paginas: list[Pagina], pos: int) -> int | None:
    for p in paginas:
        if p.inicio <= pos < p.fin:
            return p.n
    return None


def _en_limite(texto: str, ini: int, fin: int) -> bool:
    """La coincidencia empieza y termina en límite de palabra (una cita que corta «aceras» en «acera» no es exacta)."""
    antes = ini == 0 or not (texto[ini - 1].isalnum() and texto[ini].isalnum())
    despues = fin == len(texto) or not (texto[fin].isalnum() and texto[fin - 1].isalnum())
    return antes and despues


def _buscar(cita_n: str, texto_n: str, desde: int, hasta: int) -> tuple[int, int, float] | None:
    """Mejor coincidencia de `cita_n` en texto_n[desde:hasta]: (inicio, fin, similitud 0-100)."""
    zona = texto_n[desde:hasta]
    cortada: tuple[int, int] | None = None
    pos = zona.find(cita_n)
    while pos >= 0:
        ini, fin = desde + pos, desde + pos + len(cita_n)
        if _en_limite(texto_n, ini, fin):
            return ini, fin, 100.0
        cortada = cortada or (ini, fin)
        pos = zona.find(cita_n, pos + 1)
    if cortada is not None:
        return cortada[0], cortada[1], 99.0  # está, pero cortando una palabra: queda para revisión
    al = fuzz.partial_ratio_alignment(cita_n, zona, score_cutoff=UMBRAL_APROXIMADO)
    if al is None:
        return None
    return desde + al.dest_start, desde + al.dest_end, float(al.score)


def verificar(
    cita: str,
    texto: str,
    paginas: list[Pagina],
    pagina: int | None = None,
    cifra: str | None = None,
) -> Resultado:
    """Verifica `cita` contra `texto` (redactado); `paginas` en sus coordenadas; `cifra` opcional (monto citado)."""
    cita_n, _ = normalizar(cita)
    if len(cita_n) < LARGO_MINIMO:
        return Resultado("fallida", False, 0.0, motivo="cita demasiado corta para verificarse")
    texto_n, mapa = normalizar(texto)
    if not texto_n:
        return Resultado("fallida", False, 0.0, motivo="el documento no tiene texto")

    def a_original(i: int, f: int) -> tuple[int, int]:
        return mapa[i], mapa[f - 1] + 1

    # Rangos en coordenadas normalizadas de la página pedida (si existe) y, después, del documento completo.
    zonas: list[tuple[int, int]] = []
    objetivo = next((p for p in paginas if p.n == pagina), None) if pagina is not None else None
    if objetivo is not None:
        ini = next((k for k, o in enumerate(mapa) if o >= objetivo.inicio), len(mapa))
        fin = next((k for k, o in enumerate(mapa) if o >= objetivo.fin), len(mapa))
        zonas.append((ini, fin))
    zonas.append((0, len(texto_n)))

    mejor: tuple[int, int, float] | None = None
    for desde, hasta in zonas:
        encontrado = _buscar(cita_n, texto_n, desde, hasta)
        if encontrado is None:
            continue
        if mejor is None or encontrado[2] > mejor[2]:
            mejor = encontrado
        if encontrado[2] == 100.0:
            break
    if mejor is None:
        return Resultado("fallida", False, 0.0, motivo="la cita no aparece en el documento")

    ini_n, fin_n, sim = mejor
    ini, fin = a_original(ini_n, fin_n)
    real = _pagina_de(paginas, ini)
    motivo_pagina = "" if pagina is None or real == pagina else f"está en la página {real}, no en la {pagina}"

    # Cifras: la cita y el tramo encontrado deben decir los mismos números (una cifra distinta no es «parecido»).
    if sim < 100.0 and cifras(cita_n) != cifras(texto_n[ini_n:fin_n]):
        return Resultado("fallida", False, sim / 100, ini, fin, real, "las cifras de la cita difieren del texto")
    if cifra is not None:
        pedida = numero(cifra)
        if pedida is None or pedida.normalize() not in cifras(texto_n[ini_n:fin_n]):
            return Resultado(
                "fallida", False, sim / 100, ini, fin, real, "la cifra declarada no está en el texto citado"
            )

    if sim == 100.0:
        return Resultado("verificada", True, 1.0, ini, fin, real, motivo_pagina)
    return Resultado("revisar", False, sim / 100, ini, fin, real, "coincidencia aproximada: decide Daniel")
