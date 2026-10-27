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
