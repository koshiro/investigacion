"""Herramientas de los agentes, con la lista por rol impuesta EN CÓDIGO y cada llamada auditada.

- `PERMISOS` es la única fuente de qué rol puede usar qué herramienta (especificación, «Agentes»). Una llamada fuera de
  la lista se rechaza y queda en la auditoría (`herramienta.rechazada`) antes de validar argumentos o tocar nada.
- Los argumentos se validan con un modelo Pydantic por herramienta (`extra="forbid"`).
- Ninguna herramienta envía nada hacia afuera salvo `search_web` (consulta a SearXNG, auditada y acotada) y
  `request_capture` (encola una captura de un dominio de la lista blanca; la descarga la hace `capture` por el proxy).
- Las acciones largas (descubrir un canal, leer capturas, inventario) también se registran aquí para que el control
  de rol sea el mismo; su ejecución durable está en `flujos.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from investigacion import auditoria
from investigacion.buscador import Buscador, ResultadoExterno

PERMISOS: dict[str, frozenset[str]] = {
    "investigador": frozenset(
        {
            "search_sources",
            "search_web",
            "request_capture",
            "query_readonly",
            "discover_contact",
            "source_inventory",
            "draft_findings",
        }
    ),
    "lector": frozenset({"read_captures"}),
    "redactor": frozenset({"get_findings", "get_cards", "create_draft"}),
    "verificador": frozenset({"verify_citation", "get_capture_text", "verify_findings"}),
}
ROLES = tuple(PERMISOS)


class HerramientaNoPermitida(PermissionError):
    pass


class ArgumentosInvalidos(ValueError):
    pass


@dataclass
class Contexto:
    """Lo que una herramienta puede usar. `worker` es la conexión con el rol inv_worker (permisos mínimos)."""

    worker: psycopg.Connection
    investigation_id: str
    verif: psycopg.Connection | None = None
    buscador: Buscador | None = None
    gateway: Any = None
    task_id: str | None = None
    solicitante: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Herramienta:
    nombre: str
    argumentos: type[BaseModel]
    funcion: Callable[..., Any]
    descripcion: str


REGISTRO: dict[str, Herramienta] = {}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def herramienta(nombre: str, argumentos: type[BaseModel], descripcion: str):
    def registrar(fn):
        if not any(nombre in h for h in PERMISOS.values()):
            raise RuntimeError(f"herramienta {nombre} sin rol en PERMISOS")
        REGISTRO[nombre] = Herramienta(nombre, argumentos, fn, descripcion)
        return fn

    return registrar


def actor(rol: str) -> str:
    return f"agent:{rol}"


def permitida(rol: str, nombre: str) -> bool:
    return nombre in PERMISOS.get(rol, frozenset()) and nombre in REGISTRO


def autorizar(ctx: Contexto, rol: str, nombre: str, argumentos: dict) -> dict:
    """Control de rol + validación de argumentos + auditoría. Devuelve los argumentos validados o lanza."""
    objeto = f"task/{ctx.task_id}" if ctx.task_id else f"investigation/{ctx.investigation_id}"
    if not permitida(rol, nombre):
        auditoria.registrar(
            ctx.worker, actor(rol), "herramienta.rechazada", objeto, {"rol": rol, "herramienta": nombre}
        )
        raise HerramientaNoPermitida(f"el rol {rol} no puede usar {nombre}")
    h = REGISTRO[nombre]
    try:
        args = h.argumentos.model_validate(argumentos)
    except ValidationError as e:
        auditoria.registrar(
            ctx.worker,
            actor(rol),
            "herramienta.argumentos_invalidos",
            objeto,
            {"herramienta": nombre, "errores": e.error_count()},
        )
        raise ArgumentosInvalidos(f"{nombre}: {e.error_count()} argumentos inválidos") from e
    auditoria.registrar(
        ctx.worker,
        actor(rol),
        "herramienta.llamada",
        objeto,
        {"herramienta": nombre, "argumentos": {k: str(v)[:300] for k, v in args.model_dump().items()}},
    )
    return args.model_dump()


def ejecutar(ctx: Contexto, rol: str, nombre: str, argumentos: dict) -> Any:
    args = autorizar(ctx, rol, nombre, argumentos)
    return REGISTRO[nombre].funcion(ctx, rol, **args)


def catalogo() -> list[dict]:
    """Para el prompt del investigador: qué herramienta, qué roles, qué argumentos (sin datos externos)."""
    return [
        {
            "herramienta": h.nombre,
            "roles": sorted(r for r, ns in PERMISOS.items() if h.nombre in ns),
            "argumentos": sorted(h.argumentos.model_fields),
            "descripcion": h.descripcion,
        }
        for h in sorted(REGISTRO.values(), key=lambda h: h.nombre)
    ]


# --- investigador -----------------------------------------------------------------------------------------------------


class ArgsTexto(_Args):
    texto: str = Field(min_length=1, max_length=200)


@herramienta("search_sources", ArgsTexto, "Busca en el catálogo interno (dominios, fuentes y capturas) por texto.")
def search_sources(ctx: Contexto, rol: str, texto: str) -> list[dict]:
    patron = f"%{texto}%"
    dominios = ctx.worker.execute(
        "SELECT dominio, uso, estado FROM source_domain WHERE dominio ILIKE %s OR uso ILIKE %s ORDER BY dominio",
        (patron, patron),
    ).fetchall()
    capturas = ctx.worker.execute(
        "SELECT id, url_final, mime FROM capture WHERE investigation_id = %s AND url_final ILIKE %s "
        "ORDER BY fetched_at DESC LIMIT 20",
        (ctx.investigation_id, patron),
    ).fetchall()
    return [{"dominio": d, "uso": u, "estado": e} for d, u, e in dominios] + [
        {"capture_id": str(i), "url": u, "mime": m} for i, u, m in capturas
    ]


class ArgsConsulta(_Args):
    consulta: str = Field(min_length=1, max_length=200)
    maximo: int = Field(default=10, ge=1, le=10)


@herramienta(
    "search_web", ArgsConsulta, "Busca en la web con el SearXNG propio. Devuelve solo título y URL (externos)."
)
def search_web(ctx: Contexto, rol: str, consulta: str, maximo: int) -> list[ResultadoExterno]:
    if ctx.buscador is None:
        raise RuntimeError("sin buscador configurado")
    resultados = ctx.buscador.buscar(consulta, maximo)
    auditoria.registrar(
        ctx.worker,
        actor(rol),
        "busqueda.web",
        f"investigation/{ctx.investigation_id}",
        {"consulta": consulta, "resultados": len(resultados), "origen": resultados[0].origen if resultados else None},
    )
    return resultados


class ArgsUrl(_Args):
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")


def dominio_permitido(conn: psycopg.Connection, url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return (
        conn.execute(
            "SELECT 1 FROM source_domain WHERE dominio = %s AND en_lista AND cliente = 'capture'", (host,)
        ).fetchone()
        is not None
    )


@herramienta("request_capture", ArgsUrl, "Encola la captura de una URL de un dominio de la lista blanca.")
def request_capture(ctx: Contexto, rol: str, url: str) -> str:
    if not dominio_permitido(ctx.worker, url):
        auditoria.registrar(
            ctx.worker,
            actor(rol),
            "capture.fuera_de_lista",
            f"investigation/{ctx.investigation_id}",
            {"url": url[:300]},
        )
        raise HerramientaNoPermitida(f"dominio fuera de la lista blanca: {urlsplit(url).hostname}")
    jid = ctx.worker.execute(
        "INSERT INTO capture_job (created_by, investigation_id, url) VALUES (%s, %s, %s) RETURNING id",
        (actor(rol), ctx.investigation_id, url),
    ).fetchone()[0]  # type: ignore[index]
    auditoria.registrar(ctx.worker, actor(rol), "capture.pedir", f"capture_job/{jid}", {"url": url[:300]})
    return str(jid)


# Vistas de solo lectura: SQL fijo aquí; el agente elige el nombre, nunca escribe SQL.
VISTAS: dict[str, str] = {
    "capturas": "SELECT c.id::text, c.url_final, c.mime, c.fetched_at::text FROM capture c "
    "WHERE c.investigation_id = %(inv)s ORDER BY c.fetched_at DESC LIMIT %(limite)s",
    "hallazgos": "SELECT f.id::text, h.codigo, f.tipo, f.estado, f.enunciado FROM finding f "
    "LEFT JOIN hypothesis h ON h.id = f.hypothesis_id WHERE f.investigation_id = %(inv)s "
    "ORDER BY f.created_at LIMIT %(limite)s",
    "hipotesis": "SELECT codigo, enunciado, estado FROM hypothesis WHERE investigation_id = %(inv)s ORDER BY codigo "
    "LIMIT %(limite)s",
    "organismos": "SELECT o.nombre, o.dominios::text, coalesce(k.correo, k.url_portal), k.estado FROM organization o "
    "LEFT JOIN organization_contact k ON k.organization_id = o.id AND k.estado = 'confirmado' "
    "ORDER BY o.nombre LIMIT %(limite)s",
    "dominios": "SELECT dominio, estado, uso FROM source_domain ORDER BY dominio LIMIT %(limite)s",
}


class ArgsVista(_Args):
    vista: str = Field(pattern="^(" + "|".join(VISTAS) + ")$")
    limite: int = Field(default=50, ge=1, le=200)


@herramienta("query_readonly", ArgsVista, "Consulta una vista fija de solo lectura: " + ", ".join(VISTAS) + ".")
def query_readonly(ctx: Contexto, rol: str, vista: str, limite: int) -> list[list]:
    filas = ctx.worker.execute(VISTAS[vista], {"inv": ctx.investigation_id, "limite": limite}).fetchall()  # type: ignore[arg-type]
    return [list(f) for f in filas]


class ArgsOrganismo(_Args):
    organismo: str = Field(min_length=3, max_length=200)


class ArgsHipotesis(_Args):
    hipotesis: str = Field(pattern=r"^H[0-9]+$")


class ArgsNada(_Args):
    pass


class ArgsSolicitud(_Args):
    organismo: str = Field(min_length=3, max_length=200)
    pedido: str = Field(min_length=10, max_length=2000)


def _diferida(nombre: str):
    """Acción cuya ejecución durable vive en flujos.py (necesita esperar capturas o al modelo)."""

    def fn(ctx: Contexto, rol: str, **kw):
        raise RuntimeError(f"{nombre} se ejecuta como flujo, no como llamada directa")

    return fn


herramienta("discover_contact", ArgsOrganismo, "Descubre el canal de transparencia de un organismo (flujo).")(
    _diferida("discover_contact")
)
herramienta("source_inventory", ArgsNada, "Prueba cada dominio de la lista blanca y lo marca con evidencia (flujo).")(
    _diferida("source_inventory")
)
herramienta("draft_findings", ArgsHipotesis, "Redacta hallazgos de una hipótesis a partir de fichas verificadas.")(
    _diferida("draft_findings")
)
herramienta("read_captures", ArgsNada, "Lee con el lector las capturas sin ficha (no aisladas o liberadas).")(
    _diferida("read_captures")
)
herramienta("verify_findings", ArgsNada, "Verifica los hallazgos en borrador: citas en código y revisión del modelo.")(
    _diferida("verify_findings")
)
herramienta("create_draft", ArgsSolicitud, "Redacta el borrador de solicitud de transparencia a un organismo.")(
    _diferida("create_draft")
)


# --- redactor y verificador (lectura) ---------------------------------------------------------------------------------


@herramienta("get_findings", ArgsNada, "Hallazgos verificados de la investigación.")
def get_findings(ctx: Contexto, rol: str) -> list[dict]:
    filas = ctx.worker.execute(
        "SELECT f.id::text, h.codigo, f.tipo, f.enunciado FROM finding f "
        "LEFT JOIN hypothesis h ON h.id = f.hypothesis_id "
        "WHERE f.investigation_id = %s AND f.estado = 'verificado' ORDER BY h.codigo, f.created_at",
        (ctx.investigation_id,),
    ).fetchall()
    return [{"finding_id": i, "hipotesis": h, "tipo": t, "enunciado": e} for i, h, t, e in filas]


@herramienta("get_cards", ArgsNada, "Hechos de las fichas con su cita ya verificada.")
def get_cards(ctx: Contexto, rol: str) -> list[dict]:
    filas = ctx.worker.execute(
        "SELECT c.id::text, c.capture_id::text, c.pagina, c.texto_citado, k.contenido FROM citation c "
        "JOIN card k ON k.id = c.card_id JOIN capture p ON p.id = c.capture_id "
        "WHERE p.investigation_id = %s AND c.verified ORDER BY c.created_at",
        (ctx.investigation_id,),
    ).fetchall()
    return [{"citation_id": i, "capture_id": cap, "pagina": pag, "cita": t} for i, cap, pag, t, _ in filas]


class ArgsCita(_Args):
    citation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


@herramienta("verify_citation", ArgsCita, "Verifica en código una cita contra el texto redactado de su captura.")
def verify_citation(ctx: Contexto, rol: str, citation_id: str) -> dict:
    from investigacion import verificador

    if ctx.verif is None:
        raise RuntimeError("sin conexión del verificador")
    fila = ctx.verif.execute(
        "SELECT c.texto_citado, c.pagina, p.texto_redactado, p.paginas_redactadas FROM citation c "
        "JOIN pii_scan p ON p.id = c.pii_scan_id WHERE c.id = %s",
        (citation_id,),
    ).fetchone()
    if fila is None:
        raise ArgumentosInvalidos("cita inexistente")
    paginas = [verificador.Pagina(p["n"], p["inicio"], p["fin"]) for p in fila[3]]
    r = verificador.verificar(fila[0], fila[2], paginas, fila[1])
    ctx.verif.execute(
        "UPDATE citation SET verified=%s, estado=%s, similitud=%s, verificador=%s, inicio=%s, fin=%s, "
        "pagina=coalesce(%s, pagina) WHERE id=%s",
        (r.verified, r.estado, r.similitud, verificador.VERSION, r.inicio, r.fin, r.pagina, citation_id),
    )
    return {"citation_id": citation_id, "estado": r.estado, "similitud": r.similitud}


class ArgsCaptura(_Args):
    capture_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    pagina: int | None = Field(default=None, ge=1)


@herramienta("get_capture_text", ArgsCaptura, "Texto redactado de una captura (o de una página).")
def get_capture_text(ctx: Contexto, rol: str, capture_id: str, pagina: int | None) -> str:
    fila = ctx.worker.execute(
        "SELECT p.texto_redactado, p.paginas_redactadas, p.nivel, "
        "EXISTS (SELECT 1 FROM capture_release r WHERE r.pii_scan_id = p.id) FROM pii_scan p "
        "JOIN extraction e ON e.id = p.extraction_id WHERE e.capture_id = %s ORDER BY p.created_at DESC LIMIT 1",
        (capture_id,),
    ).fetchone()
    if fila is None:
        return ""
    if fila[2] == "alto" and not fila[3]:
        raise HerramientaNoPermitida("captura aislada por datos personales: requiere la aprobación de Daniel")
    if pagina is None:
        return fila[0]
    pag = next((p for p in fila[1] if p["n"] == pagina), None)
    return fila[0][pag["inicio"] : pag["fin"]] if pag else ""
