"""PDF real con docling. Requiere el extra `extract`, los modelos de docling y fpdf2; en CI sin el extra se salta."""

import shutil

import pytest

pytest.importorskip("docling")
fpdf = pytest.importorskip("fpdf")

from investigacion import extraer, redaccion, verificador  # noqa: E402


def _pdf() -> bytes:
    doc = fpdf.FPDF()
    doc.set_font("Helvetica", size=12)
    for texto in (
        "Decreto alcaldicio N 1.482. Se aprueba un monto de $ 245.800.000 para la obra Mejoramiento Calle Anfion Munoz.",
        "El inspector fiscal es Juan Perez Soto, RUN 12.345.678-5, telefono +56 9 8765 4321.",
    ):
        doc.add_page()
        doc.multi_cell(0, 8, texto)
    return bytes(doc.output())


def test_pdf_da_paginas_con_desplazamientos_y_redaccion_verificable():
    # El OCR (Tesseract) solo está en la imagen de extract; el PDF de prueba tiene texto digital.
    e = extraer.extraer_pdf(_pdf(), ocr=shutil.which("tesseract") is not None)
    assert [p.n for p in e.paginas] == [1, 2] and e.extractor.startswith("docling")
    assert "245.800.000" in e.texto[e.paginas[0].inicio : e.paginas[0].fin]
    red = redaccion.redactar(e.texto)
    assert "12.345.678" not in red.texto and "8765" not in red.texto
    paginas = [
        verificador.Pagina(p.n, redaccion.a_redactado(red.mapa, p.inicio), redaccion.a_redactado(red.mapa, p.fin))
        for p in e.paginas
    ]
    r = verificador.verificar("Se aprueba un monto de $ 245.800.000 para la obra", red.texto, paginas, 1)
    assert r.verified and r.pagina == 1


def test_rangos_de_paginas_sin_texto():
    assert extraer._rangos([9, 2, 3, 4]) == [(2, 4), (9, 9)]
    assert extraer._rangos([]) == []


def test_pdf_digital_no_necesita_ocr_y_conserva_numeracion():
    """Con texto en todas las páginas no se corre Tesseract: el extractor queda como «docling» a secas."""
    e = extraer.extraer_pdf(_pdf(), ocr=True)
    assert e.extractor == "docling" and [p.n for p in e.paginas] == [1, 2]


def test_page_range_de_docling_conserva_los_numeros_de_pagina_originales():
    import io

    from docling.datamodel.base_models import DocumentStream

    conv = extraer._conversor(ocr=False)
    doc = conv.convert(DocumentStream(name="x.pdf", stream=io.BytesIO(_pdf())), page_range=(2, 2)).document
    assert sorted(doc.pages) == [2]
