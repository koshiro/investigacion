import pytest

from investigacion import verificador as v
from tests.citas_fijas import FALSAS, PAGINAS, VERDADERAS


def _doc():
    texto = ""
    paginas = []
    for n, p in enumerate(PAGINAS, start=1):
        paginas.append(v.Pagina(n, len(texto), len(texto) + len(p)))
        texto += p
    return texto, paginas


@pytest.mark.parametrize(("cita", "pagina"), VERDADERAS)
def test_acepta_citas_verdaderas(cita, pagina):
    texto, paginas = _doc()
    r = v.verificar(cita, texto, paginas, pagina)
    assert r.estado == "verificada" and r.verified, r
    assert r.inicio is not None and r.fin is not None and r.pagina in (1, 2, 3)


@pytest.mark.parametrize(("cita", "pagina"), FALSAS)
def test_rechaza_citas_inventadas_o_alteradas(cita, pagina):
    texto, paginas = _doc()
    r = v.verificar(cita, texto, paginas, pagina)
    assert not r.verified and r.estado in ("fallida", "revisar"), r


def test_conjunto_fijo_20_y_20():
    texto, paginas = _doc()
    aceptadas = sum(v.verificar(c, texto, paginas, p).verified for c, p in VERDADERAS)
    rechazadas = sum(not v.verificar(c, texto, paginas, p).verified for c, p in FALSAS)
    assert (aceptadas, rechazadas) == (20, 20)


def test_posicion_apunta_al_texto_original():
    texto, paginas = _doc()
    r = v.verificar("orgánica constitucional de municipalidades", texto, paginas, 1)
    # El tramo original conserva el guion de corte: la posición es del texto sin normalizar.
    assert "municipali-\ndades" in texto[r.inicio : r.fin]


def test_cifra_distinta_nunca_es_aproximada():
    texto, paginas = _doc()
    r = v.verificar("Se aprueba un monto de $ 254.800.000 para la obra", texto, paginas, 1)
    assert r.estado == "fallida" and "cifras" in r.motivo


def test_cita_que_corta_una_palabra_pide_revision():
    texto, paginas = _doc()
    r = v.verificar(
        "La superficie intervenida alcanza 12.350,5 metros cuadrados de calzada y 3.200 metros de acera",
        texto,
        paginas,
        2,
    )
    assert r.estado == "revisar" and not r.verified and r.similitud >= 0.97


def test_cifra_declarada_debe_estar_en_el_texto():
    texto, paginas = _doc()
    ok = v.verificar("Se aprueba un monto de $ 245.800.000 para la obra", texto, paginas, 1, cifra="245.800.000")
    mal = v.verificar("Se aprueba un monto de $ 245.800.000 para la obra", texto, paginas, 1, cifra="245.800.001")
    assert ok.verified and not mal.verified and mal.estado == "fallida"


def test_cita_demasiado_corta_falla():
    texto, paginas = _doc()
    assert not v.verificar("de la", texto, paginas, 1).verified


def test_documento_vacio_falla_cerrado():
    assert not v.verificar("una cita cualquiera larga", "", [], 1).verified


def test_numeros_chilenos():
    assert v.numero("1.234.567,89") == v.numero("1234567,89")
    assert v.numero("1.234") == 1234
    assert str(v.numero("3,5")) == "3.5"
    assert v.cifras("monto de 1.000 y 1.000,5") != v.cifras("monto de 1.000 y 1.000,6")


def test_normalizacion_nfd_y_nfc_coinciden():
    texto = "Mejoramiento de la cámara de Villarrica en 2023"  # á descompuesta
    r = v.verificar("Mejoramiento de la cámara de Villarrica", texto, [v.Pagina(1, 0, len(texto))], 1)
    assert r.verified


def test_cambio_de_palabra_sin_cifras_pide_revision():
    texto, paginas = _doc()
    r = v.verificar(
        "La superficie intervenida alcanza 12.350,5 metros cuadrados de calzadas y 3.200 metros de aceras.",
        texto,
        paginas,
        2,
    )
    assert r.estado == "revisar" and not r.verified and r.similitud >= 0.97
