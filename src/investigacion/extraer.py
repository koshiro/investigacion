"""Extracción de texto por página con desplazamientos. Corre en `extract`, que no tiene salida de red.

PDF → docling (layout y tablas) con Tesseract en español para escaneos; los modelos van horneados en la imagen
(`DOCLING_ARTIFACTS_PATH`). CSV, texto y HTML → stdlib. Cada formato devuelve el texto completo y las páginas como
rangos [inicio, fin) de ese texto: el verificador de citas y el visor trabajan con esas coordenadas.
"""

import csv
import functools
import io
import os
import shutil
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
ESCALA_OCR = 2.5  # píxeles por punto al rasterizar una página para Tesseract (~180 dpi)
TIMEOUT_OCR_S = 180


def _ocr_pagina(pagina) -> str:  # pragma: no cover - requiere el binario tesseract
    """Rasteriza una página (pypdfium2) y la pasa por Tesseract en español. Una página por vez: memoria acotada."""
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="ocr-") as d:
        imagen = Path(d) / "pagina.png"
        pagina.render(scale=ESCALA_OCR).to_pil().convert("L").save(imagen)
        r = subprocess.run(  # noqa: S603  (argumentos fijos; la ruta es de un directorio temporal propio)
            ["tesseract", str(imagen), "stdout", "-l", "spa", "--psm", "3"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=TIMEOUT_OCR_S,
            check=True,
        )
    return r.stdout


def extraer_pdf(datos: bytes, ocr: bool = True) -> Extraido:
    """Texto nativo del PDF (pypdfium2) y OCR solo en las páginas sin capa de texto.

    Por qué no docling como motor principal (medido el 2026-10-28 con un PDF real de 9 páginas y 175 KB, la
    «Programación Financiera» de SERVIU): convertido entero superaba los 3 GiB y el pod moría por OOM; convertido
    página por página bajaba a 1,27 GiB pero entregaba `[96, 0, 11269, 12541, 0, …]` caracteres por página frente a
    `[3959, 3725, 5053, 5704, 3058, …]` del texto nativo: páginas completas casi vacías, o sea evidencia perdida sin
    aviso. El texto nativo es completo, tarda milisegundos y no usa modelos. docling queda como opción
    (`INVESTIGACION_PDF_MOTOR=docling`) para estructura de tablas; ver docs/adr/0003-extraccion-pdf.md.
    """
    import pypdfium2

    pdf = pypdfium2.PdfDocument(datos)
    textos: dict[int, str] = {}
    escasas: list[int] = []
    try:
        for i in range(len(pdf)):
            pagina = pdf[i]
            try:
                tp = pagina.get_textpage()
                try:
                    texto = tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
                finally:
                    tp.close()
            finally:
                pagina.close()
            textos[i + 1] = texto
            if len(texto.strip()) < MIN_CARACTERES_PAGINA:
                escasas.append(i + 1)
        extractor = "pdfium"
        if ocr and escasas and shutil.which("tesseract"):
            extractor = "pdfium+tesseract-spa"
            for n in escasas:
                pagina = pdf[n - 1]
                try:
                    if (t := _ocr_pagina(pagina)).strip():
                        textos[n] = t
                finally:
                    pagina.close()
    finally:
        pdf.close()
    e = unir_paginas([textos[n] for n in sorted(textos)])
    return Extraido(e.texto, e.paginas, extractor)


@functools.cache
def _conversor(ocr: bool):  # pragma: no cover - requiere el extra `extract`
    """Uno por modo y por proceso (un DocumentConverter por PDF recargaba los modelos y agotaba la memoria)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opciones = PdfPipelineOptions(
        do_ocr=ocr,
        do_table_structure=False,
        ocr_options=TesseractCliOcrOptions(lang=["spa"], scale=2.0),
        document_timeout=900,
        enable_remote_services=False,
        layout_batch_size=1,
        table_batch_size=1,
        ocr_batch_size=1,
    )
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opciones)})


def extraer_pdf_docling(datos: bytes, ocr: bool = True) -> Extraido:  # pragma: no cover - requiere el extra `extract`
    """Motor opcional (INVESTIGACION_PDF_MOTOR=docling): layout con docling, SIEMPRE una página por conversión (el
    documento entero superaba los 3 GiB). Ojo: puede dejar páginas casi vacías; comparar con el texto nativo."""
    import pypdfium2
    from docling.datamodel.base_models import DocumentStream

    def pagina(conversor, n: int) -> str:
        fuente = DocumentStream(name="captura.pdf", stream=io.BytesIO(datos))
        return conversor.convert(fuente, page_range=(n, n)).document.export_to_text(page_no=n)

    total = len(pypdfium2.PdfDocument(datos))
    sin_ocr = _conversor(ocr=False)
    textos = {n: pagina(sin_ocr, n) for n in range(1, total + 1)}
    extractor = "docling-opcional"
    escasas = [n for n, t in textos.items() if ocr and len(t.strip()) < MIN_CARACTERES_PAGINA]
    if escasas:
        extractor = "docling-opcional+tesseract-spa"
        con_ocr = _conversor(ocr=True)
        for n in escasas:
            if (t := pagina(con_ocr, n)).strip():
                textos[n] = t
    e = unir_paginas([textos[n] for n in sorted(textos)])
    return Extraido(e.texto, e.paginas, extractor)


def extraer(mime: str, datos: bytes, url: str = "") -> Extraido:
    if mime in ("application/octet-stream", "") and url.lower().split("?")[0].endswith(".csv"):
        mime = "text/csv"
    if mime == "application/pdf":
        return (
            extraer_pdf_docling(datos) if os.environ.get("INVESTIGACION_PDF_MOTOR") == "docling" else extraer_pdf(datos)
        )
    if mime in ("text/csv", "application/csv", "application/vnd.ms-excel") or mime.endswith("csv"):
        return extraer_csv(datos)
    if mime in ("text/html", "application/xhtml+xml"):
        return extraer_html(datos)
    if mime.startswith("text/") or mime in ("application/json",):
        return extraer_texto(datos)
    raise NoSoportado(f"formato sin extractor: {mime}")
