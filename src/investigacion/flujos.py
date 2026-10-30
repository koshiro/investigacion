"""Flujos durables del worker (DBOS, ADR-0002 y ADR-0004).

- `flujo_plan`: el investigador propone un plan → espera la decisión de Daniel (`DBOS.recv`, despertado por el
  despertador) → consume la aprobación con `approval_usar()` → ejecuta cada tarea como paso. Sin aprobación no corre
  nada: además del código, la base impide que una tarea pase a `en_curso` sin la aprobación usada de su plan.
- Cada tarea pasa por `herramientas.autorizar` (rol permitido, argumentos válidos, auditoría) antes de ejecutarse.
- `flujo_descubrir`, `flujo_inventario` y `flujo_leer` son flujos hijos (esperan capturas con `DBOS.sleep`).
- El despertador NO es un flujo: un hilo del worker que mira `approval` y manda `DBOS.send` (idempotente por id de
  aprobación) a los flujos que esperan. Un mensaje no aprueba nada: el flujo vuelve a leer la base.

Cada paso abre sus propias conexiones (rol inv_worker y, para verificar, inv_verificador): un reinicio retoma desde el
último paso registrado en `dbos_worker`.
"""

import socket
import threading
from dataclasses import dataclass, field
from typing import Any

import psycopg
import structlog
from dbos import DBOS
from psycopg.types.json import Jsonb

from investigacion import agentes, auditoria, descubrimiento, gateway, herramientas, lector
from investigacion.buscador import Buscador

log = structlog.get_logger()
# DBOS 3.2: las colas se registran en la base de sistema tras launch() (no se construyen a mano).
COLA = "agentes"
ESPERA_APROBACION_S = 3600
MAX_ESPERAS = 24 * 14  # dos semanas; después el flujo se cierra sin ejecutar
ESPERA_CAPTURAS_S = 20
MAX_ESPERA_CAPTURAS = 90  # ~30 minutos
# Versión fija de los flujos: por defecto DBOS usa un hash del código y un despliegue dejaría huérfano un plan que
# espera la aprobación de Daniel (días). Subirla SOLO si cambia el orden o el nombre de los pasos de un flujo: los
# flujos en curso con la versión anterior quedan sin retomar (se vuelve a pedir el plan).
VERSION_FLUJOS = "fase2-1"  # 2026-10-30: flujo_descubrir cambió, pero ningún flujo estaba dentro de él


@dataclass
class Entorno:
    worker_url: str
    verificador_url: str
    gateway: Any = None
    buscador: Buscador | None = None
    solicitante: dict[str, str] = field(default_factory=dict)
    tiene_mx: Any = descubrimiento.tiene_mx_dns
    resolver: Any = None  # dominio -> bool (existe); por defecto getaddrinfo


_ENTORNO: Entorno | None = None


def configurar(entorno: Entorno) -> None:
    global _ENTORNO
    _ENTORNO = entorno


def _env() -> Entorno:
    if _ENTORNO is None:
        raise RuntimeError("flujos sin configurar")
    return _ENTORNO


def _worker() -> psycopg.Connection:
    return psycopg.connect(_env().worker_url, autocommit=True)


def _verif() -> psycopg.Connection:
    return psycopg.connect(_env().verificador_url, autocommit=True)


def _ctx(conn, investigation_id: str, task_id: str | None = None, verif=None) -> herramientas.Contexto:
    e = _env()
    return herramientas.Contexto(conn, investigation_id, verif, e.buscador, e.gateway, task_id, e.solicitante)


# --- plan -------------------------------------------------------------------------------------------------------------


@DBOS.step()
def paso_proponer_plan(investigation_id: str, workflow_id: str) -> str:
    with _worker() as w:
        plan, costo = agentes.proponer_plan(_env().gateway, w, investigation_id)
        problemas = agentes.pasos_validos(plan)
        contenido = plan.model_dump() | {"problemas_de_permisos": problemas}
        with w.transaction():
            plan_id = str(
                w.execute(
                    "INSERT INTO plan (created_by, investigation_id, workflow_id, contenido) "
                    "VALUES ('agent:investigador', %s, %s, %s) RETURNING id",
                    (investigation_id, workflow_id, Jsonb(contenido)),
                ).fetchone()[0]  # type: ignore[index]
            )
            for i, p in enumerate(plan.pasos, 1):
                w.execute(
                    "INSERT INTO task (created_by, plan_id, orden, rol, objetivo, entradas) "
                    "VALUES ('agent:investigador', %s, %s, %s, %s, %s)",
                    (
                        plan_id,
                        i,
                        p.rol,
                        p.objetivo,
                        Jsonb(
                            {
                                "herramienta": p.herramienta,
                                "argumentos": {a.nombre: a.valor for a in p.argumentos},
                                "hipotesis": p.hipotesis,
                            }
                        ),
                    ),
                )
            auditoria.registrar(
                w,
                "agent:investigador",
                "plan.propuesto",
                f"plan/{plan_id}",
                {"pasos": len(plan.pasos), "costo_usd": costo, "problemas": len(problemas)},
            )
    return plan_id


@DBOS.step()
def paso_decision(tipo: str, objeto_id: str) -> tuple[str, str] | None:
    with _worker() as w:
        f = w.execute(
            "SELECT id::text, decision FROM approval WHERE objeto_tipo = %s AND objeto_id = %s AND used_at IS NULL "
            "ORDER BY decided_at DESC LIMIT 1",
            (tipo, objeto_id),
        ).fetchone()
    return (f[0], f[1]) if f else None


def esperar_decision(tipo: str, objeto_id: str) -> tuple[str, str] | None:
    """Dentro de un flujo: espera (durable) hasta que Daniel decide. None si nadie decidió en dos semanas."""
    for _ in range(MAX_ESPERAS):
        d = paso_decision(tipo, objeto_id)
        if d is not None:
            return d
        DBOS.recv("aprobacion", timeout_seconds=ESPERA_APROBACION_S)
    return None


@DBOS.step()
def paso_usar_aprobacion(approval_id: str, tipo: str, objeto_id: str) -> str | None:
    with _worker() as w:
        h = w.execute("SELECT content_hash FROM plan WHERE id = %s", (objeto_id,)).fetchone()
        return w.execute(
            "SELECT approval_usar(%s, %s, %s, %s, 'agent:investigador')",
            (approval_id, tipo, objeto_id, h[0] if h else ""),
        ).fetchone()[0]  # type: ignore[index]


@DBOS.step()
def paso_tareas(plan_id: str) -> list[str]:
    with _worker() as w:
        return [str(r[0]) for r in w.execute("SELECT id FROM task WHERE plan_id = %s ORDER BY orden", (plan_id,))]


@DBOS.step()
def paso_cancelar_tareas(plan_id: str, motivo: str) -> None:
    with _worker() as w:
        w.execute(
            "UPDATE task SET estado = 'cancelada', error = %s, actualizado_at = now() WHERE plan_id = %s "
            "AND estado = 'propuesta'",
            (motivo, plan_id),
        )
        auditoria.registrar(w, "agent:investigador", "plan.cerrado", f"plan/{plan_id}", {"motivo": motivo})


@DBOS.workflow()
def flujo_plan(investigation_id: str) -> str:
    plan_id = paso_proponer_plan(investigation_id, DBOS.workflow_id or "")
    DBOS.set_event("plan_id", plan_id)
    decision = esperar_decision("plan", plan_id)
    if decision is None:
        paso_cancelar_tareas(plan_id, "sin decisión de Daniel")
        return "sin decisión"
    approval_id, veredicto = decision
    if veredicto != "aprobada":
        paso_cancelar_tareas(plan_id, "plan rechazado por Daniel")
        return "rechazado"
    motivo = paso_usar_aprobacion(approval_id, "plan", plan_id)
    if motivo is not None:
        paso_cancelar_tareas(plan_id, f"aprobación inválida: {motivo}")
        return f"aprobación inválida: {motivo}"
    for task_id in paso_tareas(plan_id):
        ejecutar_tarea(investigation_id, task_id)
    return "hecho"


# --- tareas -----------------------------------------------------------------------------------------------------------


@DBOS.step()
def paso_autorizar_tarea(investigation_id: str, task_id: str) -> dict:
    """Control de rol y argumentos (auditado). Devuelve {rol, herramienta, argumentos} o {error}."""
    with _worker() as w:
        rol, entradas = w.execute("SELECT rol, entradas FROM task WHERE id = %s", (task_id,)).fetchone()  # type: ignore[misc]
        try:
            args = herramientas.autorizar(
                _ctx(w, investigation_id, task_id), rol, entradas["herramienta"], entradas.get("argumentos", {})
            )
        except (herramientas.HerramientaNoPermitida, herramientas.ArgumentosInvalidos) as e:
            w.execute(
                "UPDATE task SET estado = 'cancelada', error = %s, actualizado_at = now() WHERE id = %s",
                (str(e)[:500], task_id),
            )
            return {"error": str(e)}
        return {"rol": rol, "herramienta": entradas["herramienta"], "argumentos": args}


@DBOS.step()
def paso_marcar(task_id: str, estado: str, salida: Any = None, error: str | None = None, costo: float = 0.0) -> None:
    with _worker() as w:
        w.execute(
            "UPDATE task SET estado = %s, salida = %s, error = %s, costo_usd = costo_usd + %s, actualizado_at = now() "
            "WHERE id = %s",
            (estado, Jsonb(salida) if salida is not None else None, error, costo, task_id),
        )


def _serializable(v: Any) -> Any:
    if isinstance(v, list):
        return [_serializable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return {k: getattr(v, k) for k in v.__dataclass_fields__}
    return v


@DBOS.step()
def paso_herramienta(investigation_id: str, task_id: str, rol: str, nombre: str, argumentos: dict) -> Any:
    with _worker() as w, _verif() as v:
        return _serializable(
            herramientas.REGISTRO[nombre].funcion(_ctx(w, investigation_id, task_id, v), rol, **argumentos)
        )


def ejecutar_tarea(investigation_id: str, task_id: str) -> None:
    """Dentro de flujo_plan. Errores de una tarea la marcan fallida sin detener el resto del plan."""
    t = paso_autorizar_tarea(investigation_id, task_id)
    if "error" in t:
        return
    paso_marcar(task_id, "en_curso")
    rol, nombre, args = t["rol"], t["herramienta"], t["argumentos"]
    try:
        if nombre == "discover_contact":
            salida, costo = flujo_descubrir(investigation_id, args["organismo"]), 0.0
        elif nombre == "source_inventory":
            salida, costo = flujo_inventario(investigation_id), 0.0
        elif nombre == "read_captures":
            salida, costo = flujo_leer(investigation_id)
        elif nombre == "draft_findings":
            salida, costo = paso_hallazgos(investigation_id, task_id, args["hipotesis"])
        elif nombre == "verify_findings":
            salida, costo = paso_verificar_hallazgos(investigation_id)
        elif nombre == "create_draft":
            salida, costo = paso_borrador(investigation_id, args["organismo"], args["pedido"])
        elif nombre == "request_capture":
            jid = paso_herramienta(investigation_id, task_id, rol, nombre, args)
            salida, costo = esperar_capturas([jid]), 0.0
        else:
            salida, costo = paso_herramienta(investigation_id, task_id, rol, nombre, args), 0.0
        paso_marcar(task_id, "hecha", salida, costo=costo)
    except Exception as e:
        log.warning("tarea fallida", task_id=task_id, herramienta=nombre, error=str(e))
        paso_marcar(task_id, "fallida", error=f"{type(e).__name__}: {e}"[:500])


# --- capturas ---------------------------------------------------------------------------------------------------------


@DBOS.step()
def paso_estado_capturas(jobs: list[str]) -> dict:
    """{pendientes, listas: [(job, capture_id)], errores: [(job, error)]}. Lista = capturada y con pii_scan."""
    with _worker() as w:
        filas = w.execute(
            "SELECT j.id::text, j.estado, j.capture_id::text, j.error, EXISTS (SELECT 1 FROM pii_scan p "
            "JOIN extraction e ON e.id = p.extraction_id WHERE e.capture_id = j.capture_id) "
            "FROM capture_job j WHERE j.id = ANY(%s::uuid[])",
            (jobs,),
        ).fetchall()
    pend = sum(1 for _, est, _, _, ext in filas if est in ("pendiente", "en_curso") or (est == "hecho" and not ext))
    return {
        "pendientes": pend,
        "listas": [[j, c] for j, est, c, _, ext in filas if est == "hecho" and ext],
        "errores": [[j, err] for j, est, _, err, _ in filas if est == "error"],
    }


def esperar_capturas(jobs: list[str]) -> dict:
    estado = paso_estado_capturas(jobs)
    for _ in range(MAX_ESPERA_CAPTURAS):
        if not estado["pendientes"]:
            break
        DBOS.sleep(ESPERA_CAPTURAS_S)
        estado = paso_estado_capturas(jobs)
    return estado


@DBOS.step()
def paso_pedir_captura(investigation_id: str, url: str, rol: str = "investigador") -> str | None:
    with _worker() as w:
        try:
            return herramientas.ejecutar(_ctx(w, investigation_id), rol, "request_capture", {"url": url})
        except herramientas.HerramientaNoPermitida:
            return None


# --- descubrimiento de canales ----------------------------------------------------------------------------------------


@DBOS.step()
def paso_urls_candidatas(investigation_id: str, organismo: str) -> list[str]:
    """URLs a capturar: resultados de búsqueda en dominios oficiales del organismo o del Portal, en la lista blanca."""
    with _worker() as w:
        org = w.execute(
            "SELECT nombre, dominios, url_transparencia FROM organization WHERE nombre = %s", (organismo,)
        ).fetchone()
        if org is None:
            raise ValueError(f"organismo desconocido: {organismo}")
        ctx = _ctx(w, investigation_id)
        # La URL de transparencia conocida va primero; la búsqueda completa. Sin `site:`: duckduckgo falla con él
        # (medido el 2026-10-29); el filtro por dominio oficial lo hace el código.
        urls: list[str] = [org[2]] if org[2] and herramientas.dominio_permitido(w, org[2]) else []
        # Portadas de los dominios oficiales que están en la lista blanca: de ahí se siguen sus enlaces.
        for (dominio,) in w.execute(
            "SELECT dominio FROM source_domain WHERE en_lista AND cliente = 'capture' ORDER BY dominio"
        ).fetchall():
            if descubrimiento._en(dominio, org[1]) and f"https://{dominio}/" not in urls:
                urls.append(f"https://{dominio}/")
        consultas = [
            f"{org[0]} solicitud de acceso a la información transparencia",
            f"{org[0]} transparencia pasiva correo",
            f"{org[0]} portal transparencia",
        ]
        for c in consultas:
            try:
                resultados = herramientas.ejecutar(ctx, "investigador", "search_web", {"consulta": c[:200]})
            except Exception as e:
                log.warning("búsqueda fallida", consulta=c, error=str(e))
                continue
            for r in resultados:
                oficial = descubrimiento._en(r.dominio, org[1]) or r.dominio == descubrimiento.PORTAL
                if oficial and r.url not in urls and herramientas.dominio_permitido(w, r.url):
                    urls.append(r.url)
        return urls[:8]


@DBOS.step()
def paso_enlaces(organismo: str, capturas: list[list[str]], ya: list[str]) -> list[str]:
    """Siguiente nivel: enlaces de transparencia de las páginas oficiales ya capturadas (filtrados en código)."""
    with _worker() as w:
        dominios = w.execute("SELECT dominios FROM organization WHERE nombre = %s", (organismo,)).fetchone()[0]  # type: ignore[index]
        vistos = set(ya)
        salida: list[str] = []
        for _, capture_id in capturas:
            fila = w.execute(
                "SELECT e.enlaces, c.url_final FROM extraction e JOIN capture c ON c.id = e.capture_id "
                "WHERE e.capture_id = %s ORDER BY e.created_at DESC LIMIT 1",
                (capture_id,),
            ).fetchone()
            enlaces = descubrimiento.absolutos(fila[0], fila[1]) if fila else []
            for u in descubrimiento.enlaces_a_seguir(enlaces, dominios, vistos):
                if herramientas.dominio_permitido(w, u) and u not in salida:
                    salida.append(u)
                    vistos.add(u)
        return salida[: descubrimiento.MAX_SEGUIR]


@DBOS.step()
def paso_candidatos(organismo: str, capturas: list[list[str]]) -> list[dict]:
    """Extrae y valida candidatos (código) e inserta los nuevos en organization_contact."""
    e = _env()
    with _worker() as w:
        org_id, nombre, dominios = w.execute(
            "SELECT id, nombre, dominios FROM organization WHERE nombre = %s", (organismo,)
        ).fetchone()  # type: ignore[misc]
        salida = []
        for _, capture_id in capturas:
            url, texto, enlaces = w.execute(
                "SELECT c.url_final, e.texto, e.enlaces FROM capture c JOIN extraction e ON e.capture_id = c.id "
                "WHERE c.id = %s ORDER BY e.created_at DESC LIMIT 1",
                (capture_id,),
            ).fetchone()  # type: ignore[misc]
            propios = descubrimiento.candidatos_de_texto(texto, url, dominios, e.tiene_mx, nombre)
            de_enlaces = descubrimiento.candidatos_de_enlaces(
                texto, descubrimiento.absolutos(enlaces, url), url, dominios
            )
            for cand in propios + de_enlaces:
                existe = w.execute(
                    "SELECT 1 FROM organization_contact WHERE organization_id = %s "
                    "AND coalesce(correo, url_portal) = %s "
                    "AND estado <> 'descartado'",
                    (org_id, cand.correo or cand.url_portal),
                ).fetchone()
                if existe:
                    continue
                cid = w.execute(
                    "INSERT INTO organization_contact (created_by, organization_id, canal, correo, url_portal, tipo, "
                    "capture_id, cita, validacion, estado, last_verified_at) "
                    "VALUES ('agent:investigador', %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id",
                    (
                        org_id,
                        cand.canal,
                        cand.correo,
                        cand.url_portal,
                        cand.tipo,
                        capture_id,
                        cand.cita,
                        Jsonb(cand.validacion),
                        cand.estado,
                    ),
                ).fetchone()[0]  # type: ignore[index]
                auditoria.registrar(
                    w,
                    "agent:investigador",
                    "contacto.candidato",
                    f"organization_contact/{cid}",
                    {"organismo": nombre, "tipo": cand.tipo, "estado": cand.estado, "capture": capture_id},
                )
                salida.append({"contacto": str(cid), "canal": cand.canal, "tipo": cand.tipo, "estado": cand.estado})
    return salida


@DBOS.workflow()
def flujo_descubrir(investigation_id: str, organismo: str) -> dict:
    urls = paso_urls_candidatas(investigation_id, organismo)
    listas: list[list[str]] = []
    errores: list = []
    nivel = urls
    for _ in range(3):  # semillas y dos niveles de enlaces oficiales
        jobs = [j for j in (paso_pedir_captura(investigation_id, u) for u in nivel) if j]
        estado = esperar_capturas(jobs) if jobs else {"listas": [], "errores": [], "pendientes": 0}
        listas += estado["listas"]
        errores += estado["errores"]
        nivel = paso_enlaces(organismo, estado["listas"], urls)
        urls += nivel
        if not nivel:
            break
    candidatos = paso_candidatos(organismo, listas)
    return {"urls": urls, "capturas": len(listas), "errores": errores, "candidatos": candidatos}


# --- inventario de la lista blanca ------------------------------------------------------------------------------------


def _existe(dominio: str) -> bool:
    r = _env().resolver
    if r is not None:
        return r(dominio)
    try:
        socket.getaddrinfo(dominio, 443)
        return True
    except socket.gaierror:
        return False


@DBOS.step()
def paso_dominios_inventario() -> list[list[str]]:
    with _worker() as w:
        return [
            [d, u or f"https://{d}/"]
            for d, u in w.execute(
                "SELECT dominio, url_prueba FROM source_domain WHERE cliente = 'capture' ORDER BY dominio"
            )
        ]


@DBOS.step()
def paso_probar_dominio(investigation_id: str, dominio: str, url: str) -> dict:
    """Si el dominio no resuelve → inexistente. Si resuelve, se encola su captura (si está en la lista)."""
    with _worker() as w:
        if not _existe(dominio):
            w.execute(
                "UPDATE source_domain SET estado = 'inexistente', detalle = 'el nombre no resuelve en DNS', "
                "url_prueba = %s, revisado_at = now() WHERE dominio = %s",
                (url, dominio),
            )
            auditoria.registrar(w, "agent:investigador", "inventario.inexistente", f"source_domain/{dominio}", {})
            return {"dominio": dominio, "estado": "inexistente"}
        if not herramientas.dominio_permitido(w, url):
            w.execute(
                "UPDATE source_domain SET estado = 'no_accesible', detalle = 'fuera de la lista blanca del proxy', "
                "url_prueba = %s, revisado_at = now() WHERE dominio = %s",
                (url, dominio),
            )
            return {"dominio": dominio, "estado": "no_accesible"}
        jid = herramientas.ejecutar(_ctx(w, investigation_id), "investigador", "request_capture", {"url": url})
        return {"dominio": dominio, "job": jid}


@DBOS.step()
def paso_cerrar_dominio(dominio: str, url: str, job: str) -> dict:
    with _worker() as w:
        est, cap, err = w.execute("SELECT estado, capture_id, error FROM capture_job WHERE id = %s", (job,)).fetchone()  # type: ignore[misc]
        if est == "hecho":
            nuevo, detalle = "verificada", None
        else:
            nuevo, detalle = "no_accesible", (err or f"captura en estado {est}")[:500]
        w.execute(
            "UPDATE source_domain SET estado = %s, capture_id = %s, detalle = %s, url_prueba = %s, revisado_at = now() "
            "WHERE dominio = %s",
            (nuevo, cap, detalle, url, dominio),
        )
        auditoria.registrar(
            w,
            "agent:investigador",
            f"inventario.{nuevo}",
            f"source_domain/{dominio}",
            {"capture": str(cap) if cap else None},
        )
    return {"dominio": dominio, "estado": nuevo, "detalle": detalle}


@DBOS.workflow()
def flujo_inventario(investigation_id: str) -> list[dict]:
    resultados = []
    pendientes = []
    for dominio, url in paso_dominios_inventario():
        r = paso_probar_dominio(investigation_id, dominio, url)
        (pendientes if "job" in r else resultados).append(r if "job" not in r else [dominio, url, r["job"]])
    if pendientes:
        esperar_capturas([p[2] for p in pendientes])
        resultados += [paso_cerrar_dominio(d, u, j) for d, u, j in pendientes]
    return resultados


# --- lector, hallazgos, verificación y borradores ---------------------------------------------------------------------


@DBOS.step()
def paso_pendientes_lector(investigation_id: str) -> list[str]:
    with _worker() as w:
        return lector.pendientes(w, limite=50, investigation_id=investigation_id)


@DBOS.step()
def paso_leer(capture_id: str) -> dict:
    with _worker() as w, _verif() as v:
        try:
            return lector.leer(w, v, _env().gateway, capture_id)
        except (lector.LectorError, lector.CapturaAislada, gateway.Bloqueada, gateway.Rechazo) as e:
            # Una captura bloqueada (p. ej. un dato personal residual: el gateway falla cerrado) no detiene la lectura
            # de las demás; queda registrada en la salida de la tarea y en llm_call.
            return {"capture": capture_id, "error": f"{type(e).__name__}: {e}"}


@DBOS.workflow()
def flujo_leer(investigation_id: str) -> tuple[list[dict], float]:
    salidas = [paso_leer(c) for c in paso_pendientes_lector(investigation_id)]
    return salidas, 0.0


@DBOS.step()
def paso_hallazgos(investigation_id: str, task_id: str, codigo: str) -> tuple[dict, float]:
    with _worker() as w:
        salida, aux, costo = agentes.redactar_hallazgos(_env().gateway, w, investigation_id, codigo)
        ids = agentes.guardar_hallazgos(w, investigation_id, task_id, salida, aux)
    return {"hipotesis": codigo, "hallazgos": ids, "advertencias": salida.advertencias}, costo


@DBOS.step()
def paso_verificar_hallazgos(investigation_id: str) -> tuple[dict, float]:
    """Código primero: cada cita contra su texto; luego el modelo revisa el respaldo; el estado lo fija el código."""
    resumen = {"verificado": 0, "pendiente": 0, "rechazado": 0}
    costo = 0.0
    with _worker() as w, _verif() as v:
        hallazgos = w.execute(
            "SELECT id::text, enunciado FROM finding WHERE investigation_id = %s AND estado = 'borrador' "
            "ORDER BY created_at LIMIT 40",
            (investigation_id,),
        ).fetchall()
        ctx = _ctx(w, investigation_id, verif=v)
        listos = []
        for fid, enunciado in hallazgos:
            citas = w.execute("SELECT id::text, texto_citado FROM citation WHERE finding_id = %s", (fid,)).fetchall()
            estados = [
                herramientas.ejecutar(ctx, "verificador", "verify_citation", {"citation_id": c})["estado"]
                for c, _ in citas
            ]
            if not estados or "fallida" in estados or "pendiente" in estados:
                v.execute("UPDATE finding SET estado = 'rechazado' WHERE id = %s", (fid,))
                resumen["rechazado"] += 1
            else:
                listos.append((fid, enunciado, [t for _, t in citas], "revisar" in estados))
        if listos:
            pregunta = w.execute("SELECT pregunta FROM investigation WHERE id = %s", (investigation_id,)).fetchone()[0]  # type: ignore[index]
            por_id, costo = agentes.revisar_hallazgos(_env().gateway, [(f, e, cs) for f, e, cs, _ in listos], pregunta)
            for fid, _, _, hay_revisar in listos:
                r = por_id.get(fid)
                if r is None or not r.respaldado:
                    nuevo = "rechazado"
                elif hay_revisar:
                    nuevo = "pendiente"  # Daniel decide en la bandeja
                else:
                    nuevo = "verificado"
                v.execute("UPDATE finding SET estado = %s WHERE id = %s", (nuevo, fid))
                auditoria.registrar(
                    v,
                    "service:verificador",
                    f"hallazgo.{nuevo}",
                    f"finding/{fid}",
                    {"observaciones": (r.observaciones if r else "sin revisión del modelo")[:500]},
                )
                resumen[nuevo] += 1
    return resumen, costo


@DBOS.step()
def paso_borrador(investigation_id: str, organismo: str, pedido: str) -> tuple[dict, float]:
    with _worker() as w:
        borrador, aux, costo = agentes.redactar_solicitud(_env().gateway, w, investigation_id, organismo, pedido)
        rid = agentes.guardar_borrador(w, investigation_id, borrador, aux, _env().solicitante)
    return {"request": rid, "afirmaciones": len(borrador.afirmaciones)}, costo


# --- arranque y despertador -------------------------------------------------------------------------------------------


def iniciar(system_url: str, entorno: Entorno, esquema: str = "dbos_worker") -> None:
    configurar(entorno)
    DBOS(
        config={
            "name": "investigacion-worker",
            "system_database_url": system_url,
            "dbos_system_schema": esquema,
            "application_version": VERSION_FLUJOS,
        }
    )
    DBOS.launch()
    DBOS.register_queue(COLA, worker_concurrency=1)  # un flujo de agentes a la vez: acota el gasto y la carga


def despertar(conn: psycopg.Connection) -> int:
    """Manda un mensaje a cada flujo de plan con una decisión sin usar. Idempotente por id de aprobación."""
    filas = conn.execute(
        "SELECT a.id::text, p.workflow_id FROM approval a JOIN plan p ON p.id = a.objeto_id "
        "WHERE a.objeto_tipo = 'plan' AND a.used_at IS NULL AND p.workflow_id IS NOT NULL "
        "AND a.decided_at > now() - interval '15 days'"
    ).fetchall()
    for aid, wf in filas:
        DBOS.send(wf, aid, "aprobacion", idempotency_key=f"aprobacion-{aid}")
    return len(filas)


def despertador(worker_url: str, intervalo_s: float, parar: threading.Event) -> None:
    while not parar.is_set():
        try:
            with psycopg.connect(worker_url, autocommit=True) as c:
                iniciar_pedidos(c)
                despertar(c)
                # Sin modelo: los borradores toman el canal confirmado y los datos del solicitante en cuanto existen.
                agentes.actualizar_borradores(c, _env().solicitante)
        except Exception as e:
            log.warning("despertador", error=str(e))
        parar.wait(intervalo_s)


def iniciar_pedidos(conn: psycopg.Connection) -> int:
    """Encola un flujo de plan por cada pedido de Daniel aún no iniciado. Id de flujo fijo: no se duplica."""
    from dbos import SetWorkflowID

    n = 0
    for rid, inv in conn.execute(
        "SELECT id::text, investigation_id::text FROM plan_request WHERE iniciado_at IS NULL ORDER BY created_at"
    ).fetchall():
        wf = f"plan-{rid}"
        with SetWorkflowID(wf):
            cola = DBOS.retrieve_queue(COLA)
            if cola is None:
                raise RuntimeError(f"cola {COLA} sin registrar")
            cola.enqueue(flujo_plan, inv)
        conn.execute("UPDATE plan_request SET workflow_id = %s, iniciado_at = now() WHERE id = %s", (wf, rid))
        auditoria.registrar(conn, "service:worker", "plan.iniciado", f"plan_request/{rid}", {"workflow": wf})
        n += 1
    return n
