"""Agente `lector`: el único que ve texto de fuentes externas, y no tiene herramientas (regla 2).

Entrada: texto REDACTADO de una captura (`pii_scan`). Salida: una ficha estructurada con citas literales. El LLM no
decide nada verificable: cada cita pasa por el verificador determinista y solo ese escribe `verified`.

Dos estrategias, comparables en `evals`: (a) JSON con esquema + verificador, (b) API de Citations con la ficha
armada en código. Si la captura está aislada (nivel `alto`) el lector se niega: espera la aprobación de Daniel.
"""

import re
from typing import Literal

import psycopg
import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from investigacion import auditoria, verificador
from investigacion.gateway import Documento, Respuesta, Solicitud

log = structlog.get_logger()
ACTOR = "agent:lector"
ESQUEMA = "ficha-v1"

SISTEMA = (
    "Eres un lector de documentos públicos chilenos. Extrae solo hechos que el documento afirma de forma expresa: "
    "montos, fechas, organismos, plazos, decisiones y sus responsables. Cada hecho lleva una cita LITERAL, copiada "
    "palabra por palabra del documento (sin resumir ni corregir), y la página donde está (las páginas se marcan "
    "como [página N]). Los marcadores como [PERSONA_1] o [RUN_2] son parte del texto: déjalos tal cual. "
    "No inventes, no completes ni calcules cifras que el documento no diga. Si no hay hechos, devuelve la lista vacía. "
    "Sé breve: como máximo 60 hechos (los más relevantes), cada enunciado de hasta 200 caracteres y cada cita de hasta "
    "400 caracteres: copia el fragmento mínimo que respalda el hecho."
)


class CapturaAislada(Exception):
    """La captura contiene datos personales de nivel alto y espera la aprobación de Daniel."""


class LectorError(Exception):
    pass


# Un documento más largo no se lee entero (≈ 85 000 tokens, bajo el umbral de precio de 100 000 de Haiku 5.5): el
# CSV del BIP tiene 7 millones de caracteres. Datos tabulares masivos van por el camino determinista, no por el lector.
MAX_CARACTERES_DOCUMENTO = 300_000
MAX_TOKENS_FICHA = 8000  # una ficha con decenas de hechos y citas literales no cabe en los 4096 por defecto


def esquema_api() -> dict:
    """Esquema de la ficha para `output_config.format`. La API no admite maxItems/maxLength/minLength…: el SDK los
    quita (los pasa a la descripción) con `transform_schema`; Pydantic sigue validando los límites al recibir."""
    import anthropic

    return anthropic.transform_schema(Ficha)


class Cita(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagina: int | None = None
    texto: str = Field(min_length=1, max_length=2000)


class Hecho(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["cifra", "fecha", "monto", "organismo", "hecho"]
    enunciado: str = Field(min_length=1, max_length=600)
    valor: str | None = Field(default=None, max_length=200)
    cita: Cita


class Ficha(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hechos: list[Hecho] = Field(max_length=200)
    advertencias: list[str] = Field(default_factory=list, max_length=20)


def texto_con_paginas(texto: str, paginas: list[verificador.Pagina]) -> str:
    return "\n\n".join(f"[página {p.n}]\n{texto[p.inicio : p.fin]}" for p in paginas)


def ficha_desde_citations(bloques: list[dict], paginas: list[verificador.Pagina]) -> Ficha:
    """Estrategia (b): cada bloque de texto con citas se vuelve un hecho; la cita es `cited_text` del modelo."""
    hechos: list[Hecho] = []
    for b in bloques:
        if b.get("type") != "text" or not b.get("citations"):
            continue
        c = b["citations"][0]
        inicio = c.get("start_char_index")
        pagina = next((p.n for p in paginas if inicio is not None and p.inicio <= inicio < p.fin), None)
        hechos.append(
            Hecho(
                tipo="hecho",
                enunciado=b["text"].strip()[:600] or "(sin texto)",
                cita=Cita(pagina=pagina, texto=c["cited_text"][:2000]),
            )
        )
    return Ficha(hechos=hechos)


def _cifra_de(h: Hecho) -> str | None:
    if h.tipo not in ("cifra", "monto") or not h.valor:
        return None
    m = re.search(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?", h.valor)
    return m.group() if m else None


def leer(
    worker: psycopg.Connection,
    verif: psycopg.Connection,
    gateway,
    capture_id: str,
    estrategia: Literal["json", "citas"] = "json",
) -> dict:
    fila = worker.execute(
        "SELECT p.id, p.nivel, p.texto_redactado, p.paginas_redactadas FROM pii_scan p "
        "JOIN extraction e ON e.id = p.extraction_id WHERE e.capture_id = %s ORDER BY p.created_at DESC LIMIT 1",
        (capture_id,),
    ).fetchone()
    if fila is None:
        raise LectorError("la captura aún no tiene extracción")
    pii_id, nivel, texto, pags = fila
    if nivel == "alto":
        auditoria.registrar(worker, ACTOR, "lector.rechazada", f"capture/{capture_id}", {"motivo": "nivel alto"})
        raise CapturaAislada("captura aislada por datos personales de nivel alto: requiere aprobación")
    paginas = [verificador.Pagina(p["n"], p["inicio"], p["fin"]) for p in pags]
    if len(texto) > MAX_CARACTERES_DOCUMENTO:
        auditoria.registrar(worker, ACTOR, "lector.omitida", f"capture/{capture_id}", {"caracteres": len(texto)})
        raise LectorError(f"documento de {len(texto)} caracteres: supera el tope de {MAX_CARACTERES_DOCUMENTO}")

    if estrategia == "citas":
        sol = Solicitud(
            rol="lector",
            sistema=SISTEMA,
            documentos=[Documento(capture_id=capture_id, texto=texto)],
            instruccion="Enumera los hechos del documento, cada uno con su cita.",
            citas=True,
        )
        ficha = ficha_desde_citations(gateway.completar(sol).bloques, paginas)
        resp: Respuesta | None = None
    else:
        sol = Solicitud(
            rol="lector",
            sistema=SISTEMA,
            documentos=[Documento(capture_id=capture_id, texto=texto_con_paginas(texto, paginas))],
            instruccion="Devuelve la ficha JSON con los hechos del documento.",
            esquema=esquema_api(),
            max_tokens=MAX_TOKENS_FICHA,
        )
        ficha, resp = None, None
        for intento in range(2):
            resp = gateway.completar(sol)
            try:
                ficha = Ficha.model_validate_json(resp.texto)
                break
            except ValidationError as e:
                log.warning("ficha inválida", intento=intento, errores=e.error_count())
                sol = sol.model_copy(
                    update={
                        "instruccion": sol.instruccion
                        + f" Tu respuesta anterior no validó ({e.error_count()} errores de esquema);"
                        " respeta el esquema exactamente."
                    }
                )
        if ficha is None:
            auditoria.registrar(
                worker, ACTOR, "lector.fallo", f"capture/{capture_id}", {"motivo": "ficha inválida tras reintento"}
            )
            raise LectorError("el lector no devolvió una ficha válida")

    resumen = {"verificada": 0, "revisar": 0, "fallida": 0}
    with worker.transaction():
        card_id = worker.execute(
            "INSERT INTO card (created_by, capture_id, pii_scan_id, agente, esquema, contenido) "
            "VALUES (%s,%s,%s,'lector',%s,%s) RETURNING id",
            (ACTOR, capture_id, pii_id, f"{ESQUEMA}/{estrategia}", Jsonb(ficha.model_dump())),
        ).fetchone()[0]  # type: ignore[index]
        ids = []
        for h in ficha.hechos:
            ids.append(
                (
                    worker.execute(
                        "INSERT INTO citation (created_by, card_id, capture_id, pii_scan_id, pagina, texto_citado) "
                        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                        (ACTOR, card_id, capture_id, pii_id, h.cita.pagina, h.cita.texto),
                    ).fetchone()[0],  # type: ignore[index]
                    h,
                )
            )
        auditoria.registrar(
            worker,
            ACTOR,
            "lector.ficha",
            f"card/{card_id}",
            {"capture": capture_id, "hechos": len(ids), "estrategia": estrategia},
        )
    for cid, h in ids:
        r = verificador.verificar(h.cita.texto, texto, paginas, h.cita.pagina, _cifra_de(h))
        verif.execute(
            "UPDATE citation SET verified=%s, estado=%s, similitud=%s, verificador=%s, inicio=%s, fin=%s, "
            "pagina=coalesce(%s, pagina) WHERE id=%s",
            (r.verified, r.estado, r.similitud, verificador.VERSION, r.inicio, r.fin, r.pagina, cid),
        )
        resumen[r.estado] += 1
    auditoria.registrar(verif, "service:verificador", "citas.verificadas", f"card/{card_id}", resumen)
    return {"card_id": str(card_id), **resumen, "advertencias": ficha.advertencias}


def pendientes(conn: psycopg.Connection, limite: int = 20) -> list[str]:
    """Capturas sin ficha cuyo ESCANEO MÁS RECIENTE no es de nivel alto (alto espera la aprobación de Daniel)."""
    filas = conn.execute(
        "SELECT c.id FROM capture c JOIN LATERAL (SELECT p.nivel, e.extractor, length(p.texto_redactado) AS largo "
        "FROM pii_scan p "
        "JOIN extraction e ON e.id = p.extraction_id WHERE e.capture_id = c.id "
        "ORDER BY p.created_at DESC LIMIT 1) u ON true "
        "WHERE u.nivel <> 'alto' AND u.extractor <> 'no-soportado' AND u.largo <= %s "
        "AND NOT EXISTS (SELECT 1 FROM card k WHERE k.capture_id = c.id) ORDER BY c.created_at LIMIT %s",
        (MAX_CARACTERES_DOCUMENTO, limite),
    ).fetchall()
    return [str(f[0]) for f in filas]
