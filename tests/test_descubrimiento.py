"""Descubrimiento de canales en código: correo literal en una página oficial, clasificación y validación."""

import struct

from investigacion import descubrimiento as d

DOM = ["villarrica.cl"]
PAGINA = (
    "Transparencia Activa\n"
    "Para presentar solicitudes de acceso a la información pública (Ley N° 20.285) escriba a "
    "transparencia@villarrica.cl o en la Oficina de Partes.\n"
    "OIRS: oirs@villarrica.cl, de lunes a viernes.\n"
    "Contacto general: contacto@villarrica.cl\n"
    "Consultas del concejal: concejal.villarrica@gmail.com\n"
)


def _mx(si=("villarrica.cl", "gmail.com")):
    return lambda dominio: dominio in si


def test_encuentra_y_clasifica_cada_correo_con_su_cita_literal():
    c = {x.correo: x for x in d.candidatos_de_texto(PAGINA, "https://www.villarrica.cl/transparencia", DOM, _mx())}
    assert c["transparencia@villarrica.cl"].tipo == "transparencia"
    assert c["oirs@villarrica.cl"].tipo == "oirs"
    assert c["contacto@villarrica.cl"].tipo == "contacto_general"
    for x in c.values():
        assert x.correo in x.cita and x.cita in PAGINA
        assert x.validacion["literal_en_captura"] and x.validacion["pagina_oficial"]
    assert c["transparencia@villarrica.cl"].estado == "candidato"


def test_un_correo_de_dominio_generico_queda_en_revisar():
    c = {x.correo: x for x in d.candidatos_de_texto(PAGINA, "https://www.villarrica.cl/t", DOM, _mx())}
    g = c["concejal.villarrica@gmail.com"]
    assert g.validacion["dominio_correo"] is False and g.estado == "revisar"


def test_sin_mx_queda_en_revisar():
    c = {x.correo: x for x in d.candidatos_de_texto(PAGINA, "https://www.villarrica.cl/t", DOM, _mx(si=()))}
    assert c["transparencia@villarrica.cl"].estado == "revisar"


def test_una_pagina_fuera_de_los_dominios_oficiales_no_propone_nada():
    assert d.candidatos_de_texto(PAGINA, "https://villarrica-transparencia.com/", DOM, _mx()) == []
    assert d.candidatos_de_texto(PAGINA, "https://www.villarrica.cl.evil.example/", DOM, _mx()) == []


def test_dominio_estatal_es_valido():
    texto = "Solicitudes de acceso a la información: oficinadepartes@minvu.gob.cl"
    c = d.candidatos_de_texto(
        texto, "https://serviuaraucania.minvu.gob.cl/t", ["serviuaraucania.minvu.gob.cl"], _mx(("minvu.gob.cl",))
    )
    assert c[0].validacion["dominio_correo"] and c[0].estado == "candidato"


def test_portal_sin_correo_propone_el_canal_portal():
    texto = "Municipalidad de Villarrica\nRealice su solicitud de información a través del formulario."
    url = "https://www.portaltransparencia.cl/PortalPdT/directorio-de-organismos-regulados/?org=MU245"
    c = d.candidatos_de_texto(texto, url, DOM, _mx(), "Municipalidad de Villarrica")
    assert [(x.canal, x.url_portal, x.tipo) for x in c] == [("portal", url, "portal")]


def test_orden_prefiere_transparencia_y_validos():
    c = d.ordenar(d.candidatos_de_texto(PAGINA, "https://www.villarrica.cl/t", DOM, _mx()))
    assert c[0].correo == "transparencia@villarrica.cl" and c[-1].estado == "revisar"


def _respuesta(ident, tipos):
    nombre = b"\x0avillarrica\x02cl\x00"
    m = struct.pack(">HHHHHH", ident, 0x8180, 1, len(tipos), 0, 0) + nombre + struct.pack(">HH", 15, 1)
    for t in tipos:
        rdata = b"\x00\x0a" + nombre
        m += b"\xc0\x0c" + struct.pack(">HHIH", t, 1, 300, len(rdata)) + rdata
    return m


def test_lectura_de_respuestas_mx():
    assert d.hay_mx_en_respuesta(_respuesta(7, [15]), 7)
    assert d.hay_mx_en_respuesta(_respuesta(7, [5, 15]), 7)  # CNAME y luego MX
    assert not d.hay_mx_en_respuesta(_respuesta(7, [5]), 7)  # solo CNAME
    assert not d.hay_mx_en_respuesta(_respuesta(8, [15]), 7)  # otro id
    assert not d.hay_mx_en_respuesta(_respuesta(7, [15])[:20], 7)  # truncada
