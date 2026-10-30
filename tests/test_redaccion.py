import pytest

from investigacion import redaccion as r
from investigacion.redaccion import Hallazgo


def _rut(cuerpo: int) -> str:
    return f"{cuerpo:,}".replace(",", ".") + "-" + r.digito_verificador(cuerpo)


@pytest.mark.parametrize(
    ("cuerpo", "dv"), [(12345678, "5"), (76123456, "0"), (6, "K"), (11111111, "1"), (5126663, "3")]
)
def test_digito_verificador_conocido(cuerpo, dv):
    assert r.digito_verificador(cuerpo) == dv


def test_run_de_persona_natural_se_redacta():
    out = r.redactar(f"Don Pedro, RUN {_rut(12345678)}, firmó.")
    assert "12.345.678" not in out.texto and "[RUN_1]" in out.texto
    assert out.nivel == "alto"


def test_run_sin_puntos_con_espacios_y_k_minuscula_tambien():
    for variante in ["12345678-5", "12.345.678-5"]:
        assert "[RUN_1]" in r.redactar(f"Vecino {variante} fin").texto, variante
    k = f"{5126663}-3"
    assert "[RUN_1]" in r.redactar(f"RUT {k}").texto


def test_rut_de_empresa_puede_quedar():
    texto = f"Constructora Lago SpA, RUT {_rut(76123456)}"
    out = r.redactar(texto)
    assert out.texto == texto and out.nivel == "ninguno"


def test_rut_con_digito_invalido_solo_se_redacta_con_contexto():
    malo = "76.123.456-0" if r.digito_verificador(76123456) != "0" else "76.123.456-1"
    assert "[RUN_1]" in r.redactar(f"RUT {malo}").texto  # «RUT» delante → en duda, se redacta
    assert "[RUN_1]" in r.redactar(f"cédula de identidad: {malo}").texto
    assert malo in r.redactar(f"expediente {malo} del año").texto  # sin contexto es un número cualquiera


def test_codigo_bip_y_montos_de_tabla_no_son_run():
    """Falsos positivos reales del CSV del BIP y de la Programación Financiera de SERVIU (2026-10-28)."""
    for texto in [
        "COMPLEMENTARIO: 30403927-0 | No Correspo",
        "(DS 49) 55.338.159 - 1.813.694 10.386.533 - 2.269.726",
        "(Cifras en M$) 0 137.107 -0 0 0 0 0 -0 -0",
        "2023-12345678-5 y 1.234.567-8",
    ]:
        assert r.redactar(texto).texto == texto, texto


def test_cuerpo_entre_30_y_50_millones_solo_con_contexto():
    cuerpo = 30_403_927
    rut = f"{cuerpo}-{r.digito_verificador(cuerpo)}"
    assert r.redactar(f"código {rut}").texto == f"código {rut}"
    assert "[RUN_1]" in r.redactar(f"RUN {rut}").texto


def test_cifras_con_puntos_no_se_confunden_con_run():
    texto = "monto de $ 245.800.000 y 12.350,5 metros; decreto 1.482-2023"
    assert r.redactar(texto).texto == texto


def test_mismo_valor_mismo_marcador_y_valores_distintos_marcadores_distintos():
    a, b = _rut(12345678), _rut(9876543)
    out = r.redactar(f"{a} y {b} y otra vez {a}")
    assert out.texto == "[RUN_1] y [RUN_2] y otra vez [RUN_1]"


def test_registro_conserva_marcadores_entre_documentos():
    reg: dict = {}
    r.redactar(f"{_rut(12345678)}", registro=reg)
    out = r.redactar(f"{_rut(9876543)} luego {_rut(12345678)}", registro=reg)
    assert out.texto == "[RUN_2] luego [RUN_1]"


def test_correo_particular_se_redacta_y_el_institucional_queda():
    out = r.redactar("escriba a juan.perez@gmail.com o a transparencia@villarrica.cl o a oirs@minvu.gob.cl")
    assert "juan.perez" not in out.texto
    assert "transparencia@villarrica.cl" in out.texto and "oirs@minvu.gob.cl" in out.texto
    assert out.hallazgos == {"CORREO": 1}


def test_telefonos():
    out = r.redactar("celular +56 9 8765 4321, otro 987654321, fono: 45 241 1234, fijo +56 45 2411234")
    assert "8765" not in out.texto and "987654321" not in out.texto and "2411234" not in out.texto
    assert out.hallazgos["TELEFONO"] == 4


def test_nivel():
    assert r.redactar("nada").nivel == "ninguno"
    assert r.redactar("a@gmail.com").nivel == "bajo"
    assert r.redactar("12345678-5").nivel == "alto"


def test_detector_extra_de_nombres_se_integra():
    def nombres(texto):
        i = texto.find("María Soto")
        return [Hallazgo(i, i + 10, "PERSONA", "María Soto")] if i >= 0 else []

    out = r.redactar("La vecina María Soto vive allí; María Soto firmó.", (*r.DETECTORES_DETERMINISTAS, nombres))
    assert (
        "María" not in out.texto.split(";")[0] and out.texto.count("[PERSONA_1]") == 1
    )  # el detector solo da 1 hallazgo


def test_solapes_gana_el_mas_largo():
    def largo(_):
        return [Hallazgo(0, 10, "PERSONA", "0123456789")]

    def corto(_):
        return [Hallazgo(2, 5, "DIRECCION", "234")]

    assert r.redactar("0123456789 resto", (largo, corto)).texto == "[PERSONA_1] resto"


def test_mapa_y_traduccion_de_posiciones():
    original = f"Ficha de RUN {_rut(12345678)} aprobada. Otro texto sin datos."
    out = r.redactar(original)
    m = out.mapa[0]
    assert original[m.orig_inicio : m.orig_fin] == _rut(12345678)
    assert out.texto[m.red_inicio : m.red_fin] == "[RUN_1]"
    # Un rango posterior al marcador vuelve exacto al original.
    ini = out.texto.index("aprobada")
    o_ini, o_fin = r.a_original(out.mapa, ini, ini + len("aprobada"))
    assert original[o_ini:o_fin] == "aprobada"
    # Un rango que toca el marcador se expande al dato completo.
    o_ini, o_fin = r.a_original(out.mapa, m.red_inicio + 1, m.red_fin)
    assert original[o_ini:o_fin] == _rut(12345678)
    # Y la ida: límite de página en el original → redactado.
    pos = original.index("Otro")
    assert out.texto[r.a_redactado(out.mapa, pos) :].startswith("Otro")


def test_residuos_detecta_lo_que_queda_y_no_los_marcadores():
    assert r.residuos("ya redactado: [RUN_1] y [CORREO_2]") == []
    assert r.residuos("se coló 12.345.678-5") != []


def test_un_uuid_no_parece_un_telefono_ni_un_run():
    """Visto en vivo (2026-10-30): «9d6c69d9-8914-4559-…» daba un teléfono y el gateway bloqueaba la llamada."""
    import uuid

    from investigacion import redaccion

    assert redaccion.residuos("[cita 9d6c69d9-8914-4559-a859-ed06a350a603]") == []
    falsos = [u for u in (str(uuid.uuid4()) for _ in range(5000)) if redaccion.residuos(f"captura {u}, p. 3")]
    assert falsos == []
    # Los teléfonos reales se siguen detectando.
    assert [h.tipo for h in redaccion.residuos("Celular: 9 8914 4559")] == ["TELEFONO"]
    assert [h.tipo for h in redaccion.residuos("llame al +56 9 8914 4559.")] == ["TELEFONO"]
