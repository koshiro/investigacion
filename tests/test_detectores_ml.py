"""Requiere el extra `extract` y el modelo GLiNER (descarga única); en CI sin el extra se salta."""

import pytest

pytest.importorskip("presidio_analyzer")

from investigacion import redaccion
from investigacion.detectores_ml import detector_gliner


@pytest.fixture(scope="module")
def detector():
    return detector_gliner()


def test_nombres_y_direcciones_en_espanol(detector):
    texto = "El vecino Juan Pérez Soto vive en calle Anfión Muñoz 345, Villarrica, y reclamó por el camino."
    out = redaccion.redactar(texto, (*redaccion.DETECTORES_DETERMINISTAS, detector))
    assert "Pérez" not in out.texto and "Anfión" not in out.texto
    assert out.hallazgos.get("PERSONA", 0) >= 1 and out.hallazgos.get("DIRECCION", 0) >= 1


def test_la_misma_persona_recibe_el_mismo_marcador(detector):
    texto = "María González Tapia presentó el reclamo. Luego María González Tapia retiró el reclamo."
    out = redaccion.redactar(texto, (detector,))
    assert out.texto.count("[PERSONA_1]") == 2


def test_una_empresa_no_es_persona(detector):
    texto = "La obra la ejecutó Constructora Lago Calafquén SpA según el contrato."
    assert "Constructora Lago Calafquén SpA" in redaccion.redactar(texto, (detector,)).texto
