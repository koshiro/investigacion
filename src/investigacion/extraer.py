"""Extracción de texto por página con desplazamientos. Corre en `extract`, que no tiene salida de red.

PDF → docling (layout y tablas) con Tesseract en español para escaneos; los modelos van horneados en la imagen
(`DOCLING_ARTIFACTS_PATH`). CSV, texto y HTML → stdlib. Cada formato devuelve el texto completo y las páginas como
rangos [inicio, fin) de ese texto: el verificador de citas y el visor trabajan con esas coordenadas.
"""

import csv
import io
from dataclasses import dataclass
from html.parser import HTMLParser

VERSION_EXTRACTOR = "extractor-1"
LINEAS_POR_PAGINA = 200  # CSV y texto plano: «página» artificial para poder citar ubicaciones
SEPARADOR = "\n\n"


@dataclass(frozen=True)
class PaginaTexto:
    n: int
    inicio: int
    fin: int


@dataclass(frozen=True)
class Extraido:
    texto: str
    paginas: list[PaginaTexto]
    extractor: str
    idioma: str = "es"
    tabular: bool = False  # filas de datos (CSV): sin modelo de nombres, ver extract.procesar_capturas


class NoSoportado(Exception):
    pass


def unir_paginas(textos: list[str]) -> Extraido:
    partes, paginas, pos = [], [], 0
    for n, t in enumerate(textos, start=1):
        paginas.append(PaginaTexto(n, pos, pos + len(t)))
        partes.append(t)
        pos += len(t) + len(SEPARADOR)
    return Extraido(SEPARADOR.join(partes), paginas, VERSION_EXTRACTOR)


def decodificar(datos: bytes) -> str:
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")  # no ocurre: latin-1 decodifica todo


def extraer_texto(datos: bytes) -> Extraido:
    lineas = decodificar(datos).splitlines()
    return unir_paginas(
        ["\n".join(lineas[i : i + LINEAS_POR_PAGINA]) for i in range(0, len(lineas), LINEAS_POR_PAGINA)] or [""]
    )


def extraer_csv(datos: bytes) -> Extraido:
    """Normaliza a líneas `col1 | col2 | …` (detecta `;` o `,`); cada fila queda en una línea citable."""
    texto = decodificar(datos)
    try:
        dialecto = csv.Sniffer().sniff(texto[:8192], delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel
    filas = [" | ".join(c.strip() for c in fila) for fila in csv.reader(io.StringIO(texto), dialecto)]
    e = unir_paginas(
        ["\n".join(filas[i : i + LINEAS_POR_PAGINA]) for i in range(0, len(filas), LINEAS_POR_PAGINA)] or [""]
    )
    return Extraido(e.texto, e.paginas, e.extractor, tabular=True)


class _HtmlATexto(HTMLParser):
    _OMITIR = frozenset({"script", "style", "noscript", "template"})
    _BLOQUES = frozenset(
        {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._omitir = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._OMITIR:
            self._omitir += 1
        elif tag in self._BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self._OMITIR:
            self._omitir = max(0, self._omitir - 1)
        elif tag in self._BLOQUES:
            self.partes.append("\n")
        elif tag in ("td", "th"):
            self.partes.append(" | ")

    def handle_data(self, data):
        if not self._omitir:
            self.partes.append(data)


def extraer_html(datos: bytes) -> Extraido:
    p = _HtmlATexto()
    p.feed(decodificar(datos))
    lineas = [" ".join(linea.split()) for linea in "".join(p.partes).splitlines()]
    return extraer_texto("\n".join(linea for linea in lineas if linea).encode())


MIN_CARACTERES_PAGINA = 80  # una página con menos texto que esto se considera sin capa de texto → OCR


def _rangos(paginas: list[int]) -> list[tuple[int, int]]:
    """[2, 3, 4, 9] → [(2, 4), (9, 9)]"""
    rangos: list[tuple[int, int]] = []
    for n in sorted(paginas):
        if rangos and n == rangos[-1][1] + 1:
            rangos[-1] = (rangos[-1][0], n)
        else:
            rangos.append((n, n))
    return rangos


def _conversor(ocr: bool):  # pragma: no cover - requiere el extra `extract`
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opciones = PdfPipelineOptions(
        do_ocr=ocr,
        ocr_options=TesseractCliOcrOptions(lang=["spa"], scale=2.0),
        document_timeout=900,
        enable_remote_services=False,
        # Lotes de 1: acota la memoria (el pod tiene 4 GiB y comparte nodo).
        layout_batch_size=1,
        table_batch_size=1,
        ocr_batch_size=1,
    )
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opciones)})


def extraer_pdf(datos: bytes, ocr: bool = True) -> Extraido:  # pragma: no cover - requiere el extra `extract`
    """Texto primero, OCR solo donde falta. Medido con un PDF real de 65 páginas e imágenes: el OCR de todas las
    imágenes incrustadas (docling por defecto) lo hacía inviable; sin OCR tarda 151 s."""
    from docling.datamodel.base_models import DocumentStream

    def fuente() -> DocumentStream:
        return DocumentStream(name="captura.pdf", stream=io.BytesIO(datos))

    doc = _conversor(ocr=False).convert(fuente()).document
    textos = {n: doc.export_to_text(page_no=n) for n in sorted(doc.pages)}
    extractor = "docling"
    if ocr:
        escasas = [n for n, t in textos.items() if len(t.strip()) < MIN_CARACTERES_PAGINA]
        if escasas:
            extractor = "docling+tesseract-spa"
            conversor_ocr = _conversor(ocr=True)
            for ini, fin in _rangos(escasas):
                parcial = conversor_ocr.convert(fuente(), page_range=(ini, fin)).document
                for n in parcial.pages:
                    if n in textos and (t := parcial.export_to_text(page_no=n)).strip():
                        textos[n] = t
    e = unir_paginas([textos[n] for n in sorted(textos)])
    return Extraido(e.texto, e.paginas, extractor)


def extraer(mime: str, datos: bytes, url: str = "") -> Extraido:
    if mime in ("application/octet-stream", "") and url.lower().split("?")[0].endswith(".csv"):
        mime = "text/csv"
    if mime == "application/pdf":
        return extraer_pdf(datos)
    if mime in ("text/csv", "application/csv", "application/vnd.ms-excel") or mime.endswith("csv"):
        return extraer_csv(datos)
    if mime in ("text/html", "application/xhtml+xml"):
        return extraer_html(datos)
    if mime.startswith("text/") or mime in ("application/json",):
        return extraer_texto(datos)
    raise NoSoportado(f"formato sin extractor: {mime}")
