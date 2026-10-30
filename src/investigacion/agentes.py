"""Agentes con modelo: investigador (plan y hallazgos), redactor (borradores) y verificador (revisión).

Patrón común (ADR-0004, igual que el lector): el modelo no tiene herramientas; devuelve JSON con esquema que Pydantic
valida (un reintento) y el código decide qué se escribe. Nada de lo que viene de fuentes externas (fichas, títulos)
va en el prompt de sistema: viaja en bloques `<documento>` del gateway.

- `proponer_plan`: su entrada NO contiene contenido externo (pregunta, hipótesis, catálogo interno y herramientas),
  así que un documento hostil no puede elegir qué herramientas se llaman (plan-then-execute).
- `redactar_hallazgos`: solo puede citar citas YA verificadas de las fichas, por su id; el código copia la cita.
- `redactar_solicitud`: cada afirmación debe estar literal en el cuerpo y apuntar a un hallazgo verificado; los datos
  del solicitante los agrega el código después (nunca salen hacia la API).
- `revisar_hallazgos` (verificador): solo agrega observaciones; `verified` lo decide el verificador determinista.
"""

import json
from typing import Literal

import psycopg
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from investigacion import auditoria, herramientas
from investigacion.gateway import Documento, Solicitud

log = structlog.get_logger()
MARCA_SOLICITANTE = "{{SOLICITANTE}}"


class AgenteError(Exception):
    pass


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _esquema(modelo: type[BaseModel]) -> dict:
    import anthropic

    return anthropic.transform_schema(modelo)


def completar(gateway, sol: Solicitud, modelo: type[BaseModel]):
    """Llama al gateway y valida; un reintento con el aviso del error. Devuelve (salida, costo)."""
    costo = 0.0
    for intento in range(2):
        resp = gateway.completar(sol)
        costo += resp.costo_usd
        try:
            return modelo.model_validate_json(resp.texto), costo
        except ValidationError as e:
            log.warning("salida inválida", rol=sol.rol, intento=intento, errores=e.error_count())
            sol = sol.model_copy(
                update={
                    "instruccion": sol.instruccion + f" Tu respuesta anterior no validó ({e.error_count()} "
                    "errores de esquema); respeta el esquema exactamente."
                }
            )
    raise AgenteError(f"{sol.rol}: salida inválida tras reintento")


# --- investigador: plan -----------------------------------------------------------------------------------------------


class Argumento(_M):
    nombre: str = Field(min_length=1, max_length=40)
    valor: str = Field(max_length=2000)


class Paso(_M):
    rol: Literal["investigador", "lector", "redactor", "verificador"]
    herramienta: str = Field(min_length=1, max_length=40)
    argumentos: list[Argumento] = Field(default_factory=list, max_length=6)
    objetivo: str = Field(min_length=1, max_length=400)
    hipotesis: list[str] = Field(default_factory=list, max_length=7)


class PlanPropuesto(_M):
    objetivo: str = Field(min_length=1, max_length=600)
    pasos: list[Paso] = Field(min_length=1, max_length=40)
    limitaciones: list[str] = Field(default_factory=list, max_length=10)


SISTEMA_PLAN = (
    "Eres el investigador de una plataforma de investigación con datos públicos chilenos. Propón un plan de tareas "
    "para responder la pregunta de la investigación, poniendo a prueba cada hipótesis (sin dar ninguna por cierta). "
    "Cada paso usa UNA herramienta del catálogo, con el rol que la tiene permitida y sus argumentos exactos. Orden "
    "recomendado: inventario de fuentes, descubrimiento del canal de transparencia de cada organismo, búsqueda y "
    "captura de documentos de dominios de la lista blanca, lectura de capturas, hallazgos por hipótesis, verificación "
    "de hallazgos y un borrador de solicitud por organismo con lo que no esté publicado. Solo URLs de dominios del "
    "catálogo. Daniel aprobará el plan antes de que se ejecute: sé concreto y breve."
)


def contexto_plan(conn: psycopg.Connection, investigation_id: str) -> dict:
    """Solo datos internos: nada aquí viene de una fuente externa."""
    inv = conn.execute(
        "SELECT titulo, pregunta, periodo, territorio FROM investigation WHERE id = %s", (investigation_id,)
    ).fetchone()
    if inv is None:
        raise AgenteError("investigación inexistente")
    hip = conn.execute(
        "SELECT codigo, enunciado, la_sostiene, la_descarta, donde_mirar FROM hypothesis WHERE investigation_id = %s "
        "ORDER BY codigo",
        (investigation_id,),
    ).fetchall()
    orgs = conn.execute("SELECT nombre, dominios FROM organization ORDER BY nombre").fetchall()
    dominios = conn.execute("SELECT dominio, uso, estado, en_lista FROM source_domain ORDER BY dominio").fetchall()
    n_capturas = conn.execute(
        "SELECT count(*) FROM capture WHERE investigation_id = %s", (investigation_id,)
    ).fetchone()
    return {
        "investigacion": dict(zip(("titulo", "pregunta", "periodo", "territorio"), inv, strict=True)),
        "hipotesis": [
            dict(zip(("codigo", "enunciado", "la_sostiene", "la_descarta", "donde_mirar"), h, strict=True)) for h in hip
        ],
        "organismos": [{"nombre": n, "dominios": d} for n, d in orgs],
        "dominios": [{"dominio": d, "uso": u, "estado": e, "en_lista": li} for d, u, e, li in dominios],
        "capturas_existentes": n_capturas[0] if n_capturas else 0,
        "herramientas": herramientas.catalogo(),
    }


def proponer_plan(gateway, conn: psycopg.Connection, investigation_id: str) -> tuple[PlanPropuesto, float]:
    ctx = contexto_plan(conn, investigation_id)
    sol = Solicitud(
        rol="investigador",
        sistema=SISTEMA_PLAN,
        documentos=[],
        instruccion="Contexto interno de la investigación (JSON):\n"
        + json.dumps(ctx, ensure_ascii=False)
        + "\nDevuelve el plan como JSON según el esquema.",
        esquema=_esquema(PlanPropuesto),
        max_tokens=8000,
    )
    plan, costo = completar(gateway, sol, PlanPropuesto)
    return plan, costo


def pasos_validos(plan: PlanPropuesto) -> list[str]:
    """Problemas del plan respecto de los permisos (se muestran en la bandeja; la ejecución vuelve a comprobar)."""
    return [
        f"paso {i}: el rol {p.rol} no puede usar {p.herramienta}"
        for i, p in enumerate(plan.pasos, 1)
        if not herramientas.permitida(p.rol, p.herramienta)
    ]


# --- investigador: hallazgos ------------------------------------------------------------------------------------------


class HallazgoPropuesto(_M):
    enunciado: str = Field(min_length=1, max_length=600)
    tipo: Literal["hecho", "inferencia", "hipotesis"]
    citas: list[str] = Field(min_length=1, max_length=8, description="etiquetas de cita de la lista (c1, c2…)")
    limitaciones: list[str] = Field(default_factory=list, max_length=5)


class Hallazgos(_M):
    hallazgos: list[HallazgoPropuesto] = Field(max_length=20)
    advertencias: list[str] = Field(default_factory=list, max_length=10)


SISTEMA_HALLAZGOS = (
    "Eres el investigador. Con los hechos de las fichas (ya verificados contra sus documentos), redacta hallazgos que "
    "sostengan o debiliten la hipótesis indicada. Solo cuentan hechos sobre caminos (vialidad, pavimentación, "
    "conservación, inversión vial) de la comuna de la investigación o de los organismos que los financian o ejecutan "
    "ahí: descarta lo de otras comunas o temas (vivienda, noticias institucionales). Cada hallazgo cita una o más "
    "etiquetas de la lista (c1, c2…; no inventes etiquetas ni cifras) y dice si es hecho (lo dicen las citas), "
    "inferencia (se deduce de ellas) o hipótesis. Si los hechos no alcanzan, devuelve pocos hallazgos o ninguno y "
    "explica qué falta en advertencias."
)


def redactar_hallazgos(
    gateway, conn: psycopg.Connection, investigation_id: str, codigo: str
) -> tuple[Hallazgos, dict, float]:
    hip = conn.execute(
        "SELECT id, enunciado, la_sostiene, la_descarta FROM hypothesis WHERE investigation_id = %s AND codigo = %s",
        (investigation_id, codigo),
    ).fetchone()
    if hip is None:
        raise AgenteError(f"hipótesis {codigo} inexistente")
    citas = conn.execute(
        "SELECT c.id::text, c.capture_id::text, c.pii_scan_id::text, c.pagina, c.texto_citado FROM citation c "
        "JOIN capture p ON p.id = c.capture_id WHERE p.investigation_id = %s AND c.card_id IS NOT NULL AND c.verified "
        "ORDER BY c.created_at LIMIT 400",
        (investigation_id,),
    ).fetchall()
    # Etiquetas cortas (c1, c2…) en vez de UUID: un UUID puede parecer un teléfono para el detector y bloquear la
    # llamada (medido el 2026-10-30); además el modelo no necesita los ids internos. El código traduce de vuelta.
    por_id = {f"c{i}": c for i, c in enumerate(citas, 1)}
    texto = "\n".join(f"[{e}] (p. {c[3] or '?'}) «{c[4]}»" for e, c in por_id.items())
    sol = Solicitud(
        rol="investigador",
        sistema=SISTEMA_HALLAZGOS,
        documentos=[Documento(capture_id="fichas", texto=texto or "(sin citas verificadas)")],
        instruccion=f"Hipótesis {codigo}: {hip[1]}. La sostiene: {hip[2]}. La descarta: {hip[3]}. "
        "Devuelve los hallazgos como JSON según el esquema.",
        esquema=_esquema(Hallazgos),
        max_tokens=6000,
    )
    salida, costo = completar(gateway, sol, Hallazgos)
    return salida, {"hypothesis_id": str(hip[0]), "citas": por_id}, costo


def guardar_hallazgos(
    worker: psycopg.Connection, investigation_id: str, task_id: str | None, salida: Hallazgos, aux: dict
) -> list[str]:
    """Inserta hallazgos y copia sus citas desde las fichas. Un id de cita que no existe descarta el hallazgo."""
    ids = []
    for h in salida.hallazgos:
        citas = [aux["citas"].get(c) for c in h.citas]
        if not citas or any(c is None for c in citas):
            auditoria.registrar(
                worker,
                "agent:investigador",
                "hallazgo.descartado",
                f"investigation/{investigation_id}",
                {"motivo": "cita inexistente", "enunciado": h.enunciado[:200]},
            )
            continue
        with worker.transaction():
            fid = worker.execute(
                "INSERT INTO finding (created_by, investigation_id, hypothesis_id, task_id, enunciado, tipo, estado, "
                "limitaciones) VALUES ('agent:investigador', %s, %s, %s, %s, %s, 'borrador', %s) RETURNING id",
                (investigation_id, aux["hypothesis_id"], task_id, h.enunciado, h.tipo, json.dumps(h.limitaciones)),
            ).fetchone()[0]  # type: ignore[index]
            for c in citas:
                worker.execute(
                    "INSERT INTO citation (created_by, finding_id, capture_id, pii_scan_id, pagina, texto_citado) "
                    "VALUES ('agent:investigador', %s, %s, %s, %s, %s)",
                    (fid, c[1], c[2], c[3], c[4]),
                )
            auditoria.registrar(
                worker, "agent:investigador", "hallazgo.borrador", f"finding/{fid}", {"citas": len(citas)}
            )
        ids.append(str(fid))
    return ids


# --- verificador ------------------------------------------------------------------------------------------------------


class Revision(_M):
    finding_id: str
    respaldado: bool
    observaciones: str = Field(default="", max_length=600)


class Revisiones(_M):
    revisiones: list[Revision] = Field(max_length=40)


SISTEMA_VERIFICADOR = (
    "Eres el verificador. Para cada hallazgo, decide si sus citas literales lo respaldan por completo Y si es "
    "pertinente a la investigación indicada (caminos de esa comuna): respaldado = true solo si ambas cosas se "
    "cumplen; false si afirma más de lo que dicen las citas o si trata de otro territorio o tema. Explica en "
    "observaciones qué falta, qué se contradice o por qué no es pertinente. No evalúes si las citas existen: eso ya "
    "se comprobó en código."
)


def revisar_hallazgos(
    gateway, filas: list[tuple[str, str, list[str]]], pregunta: str = ""
) -> tuple[dict[str, Revision], float]:
    """filas: (finding_id, enunciado, [citas literales]). Devuelve {finding_id: revisión}; el modelo ve h1, h2…"""
    etiquetas = {f"h{i}": f for i, (f, _, _) in enumerate(filas, 1)}
    texto = "\n\n".join(
        f"[h{i}] {e}\n" + "\n".join(f"  cita: «{c}»" for c in cs) for i, (_, e, cs) in enumerate(filas, 1)
    )
    sol = Solicitud(
        rol="verificador",
        sistema=SISTEMA_VERIFICADOR,
        documentos=[Documento(capture_id="hallazgos", texto=texto)],
        instruccion=f"Investigación: {pregunta}\nDevuelve una revisión por hallazgo (finding_id = h1, h2…) como JSON "
        "según el esquema.",
        esquema=_esquema(Revisiones),
        max_tokens=6000,
    )
    revisiones, costo = completar(gateway, sol, Revisiones)
    return {etiquetas[r.finding_id]: r for r in revisiones.revisiones if r.finding_id in etiquetas}, costo


# --- redactor ---------------------------------------------------------------------------------------------------------


class Afirmacion(_M):
    texto: str = Field(min_length=5, max_length=600)
    finding_id: str


class Borrador(_M):
    asunto: str = Field(min_length=5, max_length=200)
    cuerpo: str = Field(min_length=50, max_length=8000)
    afirmaciones: list[Afirmacion] = Field(default_factory=list, max_length=20)


SISTEMA_REDACTOR = (
    "Eres el redactor de solicitudes de acceso a la información pública (Ley 20.285, artículo 12). Escribe una "
    "solicitud corta y cordial al organismo indicado que identifique con claridad la información pedida, en formato "
    "reutilizable (planilla o CSV) cuando corresponda, y el órgano destinatario. Si mencionas un hecho de contexto, "
    "cópialo literal de un hallazgo verificado y lístalo en afirmaciones con su etiqueta (h1, h2…) como finding_id. "
    "No escribas nombre, "
    "correo ni firma del solicitante: pon exactamente " + MARCA_SOLICITANTE + " donde van (el sistema los agrega)."
)


def redactar_solicitud(
    gateway, conn: psycopg.Connection, investigation_id: str, organismo: str, pedido: str
) -> tuple[Borrador, dict, float]:
    org = conn.execute("SELECT id, nombre FROM organization WHERE nombre = %s", (organismo,)).fetchone()
    if org is None:
        raise AgenteError(f"organismo desconocido: {organismo}")
    hallazgos = herramientas.get_findings(herramientas.Contexto(conn, investigation_id), "redactor")
    etiquetas = {f"h{i}": h["finding_id"] for i, h in enumerate(hallazgos, 1)}
    texto = "\n".join(f"[h{i}] ({h['hipotesis']}, {h['tipo']}) {h['enunciado']}" for i, h in enumerate(hallazgos, 1))
    sol = Solicitud(
        rol="redactor",
        sistema=SISTEMA_REDACTOR,
        documentos=[Documento(capture_id="hallazgos", texto=texto or "(sin hallazgos verificados)")],
        instruccion=f"Organismo destinatario: {org[1]}. Qué pedir: {pedido}. Devuelve el borrador como JSON.",
        esquema=_esquema(Borrador),
        max_tokens=4000,
    )
    borrador, costo = completar(gateway, sol, Borrador)
    return borrador, {"organization_id": str(org[0]), "verificados": etiquetas}, costo


def bloque_solicitante(solicitante: dict[str, str]) -> str:
    nombre = solicitante.get("nombre") or "[NOMBRE DEL SOLICITANTE]"
    correo = solicitante.get("correo") or "[CORREO PARA NOTIFICACIONES]"
    return f"{nombre}\nCorreo para notificaciones: {correo}"


def actualizar_borradores(worker: psycopg.Connection, solicitante: dict[str, str]) -> list[str]:
    """Sin modelo: versión nueva de cada borrador vigente (sin decisión de Daniel) cuando ya hay datos del solicitante o
    un canal confirmado que el borrador no tiene. Copia asunto, cuerpo y afirmaciones; solo cambia el bloque del
    solicitante y el destinatario. Devuelve los ids nuevos."""
    marcador = bloque_solicitante({})
    real = bloque_solicitante(solicitante) if solicitante.get("nombre") and solicitante.get("correo") else None
    filas = worker.execute(
        "SELECT r.id, r.investigation_id, r.organization_id, r.contact_id, r.asunto, r.cuerpo, "
        "(SELECT k.id FROM organization_contact k WHERE k.organization_id = r.organization_id "
        " AND k.estado = 'confirmado' ORDER BY k.confirmed_at DESC LIMIT 1) "
        "FROM request r WHERE r.estado IN ('borrador', 'en_revision') "
        "AND NOT EXISTS (SELECT 1 FROM request n WHERE n.anterior_id = r.id) "
        "AND NOT EXISTS (SELECT 1 FROM approval a WHERE a.objeto_tipo = 'request' AND a.objeto_id = r.id)"
    ).fetchall()
    nuevos = []
    for rid, inv, org, contacto, asunto, cuerpo, confirmado in filas:
        cuerpo_nuevo = cuerpo.replace(marcador, real) if real and marcador in cuerpo else cuerpo
        contacto_nuevo = confirmado or contacto
        if cuerpo_nuevo == cuerpo and contacto_nuevo == contacto:
            continue
        with worker.transaction():
            nid = worker.execute(
                "INSERT INTO request (created_by, investigation_id, organization_id, contact_id, anterior_id, asunto, "
                "cuerpo) VALUES ('agent:redactor', %s, %s, %s, %s, %s, %s) RETURNING id",
                (inv, org, contacto_nuevo, rid, asunto, cuerpo_nuevo),
            ).fetchone()[0]  # type: ignore[index]
            worker.execute(
                "INSERT INTO request_claim (created_by, request_id, finding_id, texto) "
                "SELECT 'agent:redactor', %s, finding_id, texto FROM request_claim WHERE request_id = %s",
                (nid, rid),
            )
            auditoria.registrar(
                worker,
                "agent:redactor",
                "borrador.actualizar",
                f"request/{nid}",
                {"anterior": str(rid), "solicitante": cuerpo_nuevo != cuerpo, "canal": contacto_nuevo != contacto},
            )
        nuevos.append(str(nid))
    return nuevos


def guardar_borrador(
    worker: psycopg.Connection, investigation_id: str, borrador: Borrador, aux: dict, solicitante: dict[str, str]
) -> str:
    """Valida en código y guarda una versión nueva del borrador (anterior_id = la última del mismo organismo)."""
    malas = [
        a for a in borrador.afirmaciones if a.finding_id not in aux["verificados"] or a.texto not in borrador.cuerpo
    ]
    if malas:
        auditoria.registrar(
            worker,
            "agent:redactor",
            "borrador.rechazado",
            f"investigation/{investigation_id}",
            {"motivo": "afirmación sin hallazgo verificado o no literal en el cuerpo", "n": len(malas)},
        )
        raise AgenteError(f"{len(malas)} afirmaciones sin hallazgo verificado o no literales en el cuerpo")
    cuerpo = borrador.cuerpo
    datos = bloque_solicitante(solicitante)
    cuerpo = cuerpo.replace(MARCA_SOLICITANTE, datos) if MARCA_SOLICITANTE in cuerpo else f"{cuerpo}\n\n{datos}"
    org = aux["organization_id"]
    with worker.transaction():
        contacto = worker.execute(
            "SELECT id FROM organization_contact WHERE organization_id = %s AND estado = 'confirmado' "
            "ORDER BY confirmed_at DESC LIMIT 1",
            (org,),
        ).fetchone()
        anterior = worker.execute(
            "SELECT id FROM request r WHERE organization_id = %s AND investigation_id = %s "
            "AND NOT EXISTS (SELECT 1 FROM request n WHERE n.anterior_id = r.id) ORDER BY created_at DESC LIMIT 1",
            (org, investigation_id),
        ).fetchone()
        rid = worker.execute(
            "INSERT INTO request (created_by, investigation_id, organization_id, contact_id, anterior_id, asunto, "
            "cuerpo) "
            "VALUES ('agent:redactor', %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                investigation_id,
                org,
                contacto[0] if contacto else None,
                anterior[0] if anterior else None,
                borrador.asunto,
                cuerpo,
            ),
        ).fetchone()[0]  # type: ignore[index]
        for a in borrador.afirmaciones:
            worker.execute(
                "INSERT INTO request_claim (created_by, request_id, finding_id, texto) "
                "VALUES ('agent:redactor', %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (rid, aux["verificados"][a.finding_id], a.texto),
            )
        auditoria.registrar(
            worker, "agent:redactor", "borrador.crear", f"request/{rid}", {"afirmaciones": len(borrador.afirmaciones)}
        )
    return str(rid)
