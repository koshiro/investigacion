"""PDF: texto nativo por página (pypdfium2) con OCR solo donde falta la capa de texto. Corre en el CI (sin modelos)."""

import fpdf
import pytest

from investigacion import extraer


def _pdf(*paginas: str) -> bytes:
    doc = fpdf.FPDF()
    doc.set_font("Helvetica", size=12)
    for texto in paginas:
        doc.add_page()
        if texto:
            doc.multi_cell(0, 8, texto)
    return bytes(doc.output())


TEXTO1 = "Decreto alcaldicio N 1.482. Se aprueba un monto de $ 245.800.000 para la obra Mejoramiento Calle Anfion."
TEXTO2 = "El plazo de ejecucion sera de 180 dias corridos. El financiamiento proviene del FNDR con aporte del 15%."


def test_texto_nativo_completo_por_pagina_con_desplazamientos_exactos():
    e = extraer.extraer_pdf(_pdf(TEXTO1, TEXTO2), ocr=False)
    assert e.extractor == "pdfium" and [p.n for p in e.paginas] == [1, 2]
    p1, p2 = e.paginas
    assert "245.800.000" in e.texto[p1.inicio : p1.fin] and "180 dias corridos" in e.texto[p2.inicio : p2.fin]
    assert "180 dias" not in e.texto[p1.inicio : p1.fin]


def test_cobertura_frente_al_texto_nativo_de_pdfium():
    """Regresión del hallazgo del 2026-10-28: ninguna página puede quedar vacía si el PDF trae texto."""
    import pypdfium2

    datos = _pdf(TEXTO1, TEXTO2)
    nativo = [
        len(pypdfium2.PdfDocument(datos)[i].get_textpage().get_text_range().replace("\r\n", "\n")) for i in range(2)
    ]
    obtenido = [p.fin - p.inicio for p in extraer.extraer_pdf(datos, ocr=False).paginas]
    assert obtenido == nativo and all(n > 80 for n in obtenido)


def test_pagina_sin_texto_va_a_ocr_solo_esa_pagina(monkeypatch):
    llamadas = []
    monkeypatch.setattr(extraer.shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(
        extraer, "_ocr_pagina", lambda pagina: llamadas.append(1) or "Texto leído por OCR de la página escaneada."
    )
    e = extraer.extraer_pdf(_pdf(TEXTO1, "", TEXTO2), ocr=True)
    assert len(llamadas) == 1 and e.extractor == "pdfium+tesseract-spa"
    textos = [e.texto[p.inicio : p.fin] for p in e.paginas]
    assert "OCR" in textos[1] and "OCR" not in textos[0] and "OCR" not in textos[2]


def test_sin_ocr_o_sin_tesseract_la_pagina_vacia_queda_vacia(monkeypatch):
    monkeypatch.setattr(extraer, "_ocr_pagina", lambda pagina: pytest.fail("no debía llamarse"))
    e = extraer.extraer_pdf(_pdf(TEXTO1, ""), ocr=False)
    assert e.extractor == "pdfium" and e.texto[e.paginas[1].inicio : e.paginas[1].fin].strip() == ""
    monkeypatch.setattr(extraer.shutil, "which", lambda _: None)
    assert extraer.extraer_pdf(_pdf(TEXTO1, ""), ocr=True).extractor == "pdfium"


def test_motor_docling_solo_con_la_variable(monkeypatch):
    llamado = []
    monkeypatch.setattr(
        extraer, "extraer_pdf_docling", lambda datos: llamado.append(1) or extraer.Extraido("", [], "x")
    )
    extraer.extraer("application/pdf", _pdf(TEXTO1))
    assert llamado == []
    monkeypatch.setenv("INVESTIGACION_PDF_MOTOR", "docling")
    extraer.extraer("application/pdf", _pdf(TEXTO1))
    assert llamado == [1]
