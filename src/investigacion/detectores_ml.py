"""Detector de nombres y direcciones: reconocedor GLiNER multilingüe de Presidio (solo imagen `extract`).

El modelo se hornea en la imagen al construirla (`HF_HOME` dentro de la imagen) porque `extract` no tiene red.
Importación perezosa: el resto del paquete (web, gateway) no necesita torch.
"""

from investigacion.redaccion import Detector, Hallazgo

MODELO = "urchade/gliner_multi_pii-v1"
# Etiquetas del modelo (texto libre, en español) → tipo de marcador.
CHUNK_CARACTERES = 900
ETIQUETAS = {"persona": "PERSONA", "dirección": "DIRECCION"}


def detector_gliner(umbral: float = 0.5, modelo: str = MODELO) -> Detector:
    from presidio_analyzer.predefined_recognizers import GLiNERRecognizer

    reconocedor = GLiNERRecognizer(
        entity_mapping=ETIQUETAS, model_name=modelo, supported_language="es", threshold=umbral
    )

    def detectar(texto: str) -> list[Hallazgo]:
        return [
            Hallazgo(r.start, r.end, r.entity_type, texto[r.start : r.end])
            for r in reconocedor.analyze(texto, list(ETIQUETAS.values()))
        ]

    return detectar
