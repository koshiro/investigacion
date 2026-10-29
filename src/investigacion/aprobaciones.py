"""Bandeja de aprobaciones: qué espera la decisión de Daniel, su contenido exacto y qué pasa al decidir.

La base impone las reglas (migración 0006, ADR-0004); este módulo solo arma lo que se muestra y aplica la decisión con
el rol de `web`. El hash que se aprueba es siempre el que calcula la base sobre el contenido vigente: si cambió entre
que Daniel lo vio y lo aprobó, la aprobación se rechaza.
"""

import difflib
import json
from dataclasses import dataclass, field

import psycopg

from investigacion import auditoria

# Lo que se aprueba en la bandeja de la fase 2. `request` se aprueba aquí, pero lo consume el dispatcher (fase 3).
TIPOS = {
    "plan": "Plan de tareas",
    "organization_contact": "Canal de transparencia",
    "finding": "Hallazgo con citas en revisar",
    "capture_release": "Sacar una captura del aislamiento",
    "request": "Borrador de solicitud",
}
# La web consume de inmediato estas aprobaciones y aplica el cambio; el plan lo consume el worker y la solicitud el
# dispatcher.
CONSUME_WEB = {"organization_contact", "finding", "capture_release"}
MAX_TEXTO = 200_000


class ContenidoCambiado(Exception):
    """El hash que vio Daniel no es el del contenido vigente."""


@dataclass
class Objeto:
    tipo: str
    id: str
    titulo: str
    contenido: str
    hash: str
    anterior: str | None = None
    enlaces: list[tuple[str, str]] = field(default_factory=list)  # (texto, url interna)

    @property
    def diff(self) -> str:
        if self.anterior is None:
            return ""
        return "\n".join(
            difflib.unified_diff(
                self.anterior.splitlines(), self.contenido.splitlines(), "versión anterior", "esta versión", lineterm=""
            )
        )


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False, indent=2, sort_keys=True)


def cargar(conn: psycopg.Connection, tipo: str, oid: str) -> Objeto | None:
    if tipo == "plan":
        f = conn.execute(
            "SELECT p.contenido, p.content_hash, a.contenido, i.titulo FROM plan p "
            "JOIN investigation i ON i.id = p.investigation_id LEFT JOIN plan a ON a.id = p.anterior_id "
            "WHERE p.id = %s",
            (oid,),
        ).fetchone()
        if f is None:
            return None
        return Objeto(tipo, oid, f"Plan de tareas · {f[3]}", _json(f[0]), f[1], _json(f[2]) if f[2] else None)
    if tipo == "organization_contact":
        f = conn.execute(
            "SELECT o.nombre, k.canal, k.correo, k.url_portal, k.tipo, k.capture_id, c.url_final, k.cita, "
            "k.validacion, k.content_hash, k.estado FROM organization_contact k "
            "JOIN organization o ON o.id = k.organization_id JOIN capture c ON c.id = k.capture_id WHERE k.id = %s",
            (oid,),
        ).fetchone()
        if f is None:
            return None
        lineas = [
            f"Organismo: {f[0]}",
            f"Canal: {f[1]}",
            f"Correo: {f[2] or '—'}",
            f"Portal: {f[3] or '—'}",
            f"Tipo: {f[4]}",
            f"Captura: {f[5]} ({f[6]})",
            f"Cita literal: «{f[7]}»",
            f"Validación en código: {_json(f[8])}",
            f"Estado actual: {f[10]}",
        ]
        return Objeto(tipo, oid, f"Canal de transparencia · {f[0]}", "\n".join(lineas), f[9], None,
                      [("Ver la captura", f"/capturas/{f[5]}")])  # fmt: skip
    if tipo == "finding":
        f = conn.execute(
            "SELECT f.enunciado, f.tipo, h.codigo, f.limitaciones, f.content_hash, f.estado FROM finding f "
            "LEFT JOIN hypothesis h ON h.id = f.hypothesis_id WHERE f.id = %s",
            (oid,),
        ).fetchone()
        if f is None:
            return None
        citas = conn.execute(
            "SELECT capture_id, pagina, texto_citado, estado, similitud FROM citation WHERE finding_id = %s "
            "ORDER BY created_at",
            (oid,),
        ).fetchall()
        lineas = [f"Enunciado: {f[0]}", f"Tipo: {f[1]}", f"Hipótesis: {f[2] or '—'}", f"Estado: {f[5]}",
                  f"Limitaciones: {_json(f[3])}", "Citas:"]  # fmt: skip
        lineas += [
            f"- [{c[3]}{f' {c[4]:.3f}' if c[4] is not None else ''}] captura {c[0]} p. {c[1] or '?'}: «{c[2]}»"
            for c in citas
        ]
        enlaces = [(f"Captura {c[0]}", f"/capturas/{c[0]}") for c in {c[0]: c for c in citas}.values()]
        return Objeto(tipo, oid, f"Hallazgo · {f[0][:80]}", "\n".join(lineas), f[4], None, enlaces)
    if tipo == "capture_release":
        f = conn.execute(
            "SELECT p.texto_redactado, hash_texto(p.texto_redactado), p.hallazgos, e.capture_id, c.url_final "
            "FROM pii_scan p JOIN extraction e ON e.id = p.extraction_id JOIN capture c ON c.id = e.capture_id "
            "WHERE p.id = %s AND p.nivel = 'alto'",
            (oid,),
        ).fetchone()
        if f is None:
            return None
        texto = f[0] if len(f[0]) <= MAX_TEXTO else f[0][:MAX_TEXTO] + "\n[… recortado en la vista; el hash cubre todo]"
        contenido = f"Captura: {f[3]} ({f[4]})\nDatos personales detectados: {_json(f[2])}\n\nTexto redactado que " \
                    f"llegaría al lector:\n{texto}"  # fmt: skip
        return Objeto(tipo, oid, f"Liberar captura aislada · {f[4][:80]}", contenido, f[1], None,
                      [("Ver la captura", f"/capturas/{f[3]}")])  # fmt: skip
    if tipo == "request":
        f = conn.execute(
            "SELECT o.nombre, k.correo, k.url_portal, r.asunto, r.cuerpo, r.content_hash, a.asunto, a.cuerpo "
            "FROM request r JOIN organization o ON o.id = r.organization_id "
            "LEFT JOIN organization_contact k ON k.id = r.contact_id LEFT JOIN request a ON a.id = r.anterior_id "
            "WHERE r.id = %s",
            (oid,),
        ).fetchone()
        if f is None:
            return None
        afirmaciones = conn.execute(
            "SELECT c.texto, c.finding_id FROM request_claim c WHERE c.request_id = %s ORDER BY c.created_at", (oid,)
        ).fetchall()

        def armar(asunto, cuerpo):
            return f"Para: {f[0]} <{f[1] or f[2] or 'sin canal confirmado'}>\nAsunto: {asunto}\n\n{cuerpo}"

        contenido = (
            armar(f[3], f[4])
            + "\n\nAfirmaciones y su hallazgo verificado:\n"
            + "\n".join(f"- «{a[0]}» → hallazgo {a[1]}" for a in afirmaciones)
        )
        return Objeto(tipo, oid, f"Solicitud · {f[0]}", contenido, f[5], armar(f[6], f[7]) if f[6] else None)
    return None


def pendientes(conn: psycopg.Connection) -> list[tuple[str, str, str]]:
    """(tipo, id, resumen) de todo lo que espera una decisión, lo más antiguo primero."""
    sin_decision = "NOT EXISTS (SELECT 1 FROM approval a WHERE a.objeto_tipo = %(t)s AND a.objeto_id = obj.id)"
    consultas = {
        "plan": "SELECT obj.id, 'Plan de ' || i.titulo, obj.created_at FROM plan obj "
        "JOIN investigation i ON i.id = obj.investigation_id "
        "WHERE NOT EXISTS (SELECT 1 FROM plan n WHERE n.anterior_id = obj.id)",
        "organization_contact": "SELECT obj.id, o.nombre || ': ' || coalesce(obj.correo, obj.url_portal) || "
        "' (' || obj.tipo || ')', obj.created_at FROM organization_contact obj "
        "JOIN organization o ON o.id = obj.organization_id WHERE obj.estado IN ('candidato', 'revisar')",
        "finding": "SELECT obj.id, left(obj.enunciado, 120), obj.created_at FROM finding obj "
        "WHERE obj.estado = 'pendiente' "
        "AND EXISTS (SELECT 1 FROM citation c WHERE c.finding_id = obj.id AND c.estado = 'revisar')",
        "capture_release": "SELECT obj.id, c.url_final, obj.created_at FROM capture c "
        "JOIN LATERAL (SELECT p.id, p.nivel, p.created_at FROM pii_scan p "
        "JOIN extraction e ON e.id = p.extraction_id WHERE e.capture_id = c.id "
        "ORDER BY p.created_at DESC LIMIT 1) obj ON true WHERE obj.nivel = 'alto' "
        "AND NOT EXISTS (SELECT 1 FROM capture_release r WHERE r.pii_scan_id = obj.id)",
        "request": "SELECT obj.id, o.nombre || ': ' || obj.asunto, obj.created_at FROM request obj "
        "JOIN organization o ON o.id = obj.organization_id WHERE obj.estado IN ('borrador', 'en_revision') "
        "AND NOT EXISTS (SELECT 1 FROM request n WHERE n.anterior_id = obj.id)",
    }
    filas = []
    for tipo, sql in consultas.items():
        # SQL fijo del módulo; el tipo va como parámetro.
        filas += [(tipo, str(i), r, c) for i, r, c in conn.execute(sql + " AND " + sin_decision, {"t": tipo})]  # type: ignore[arg-type]
    return [(t, i, r) for t, i, r, _ in sorted(filas, key=lambda f: f[3])]


def decidir(
    conn: psycopg.Connection, obj: Objeto, hash_visto: str, decision: str, totp_paso: int, comentario: str = ""
) -> str:
    """Crea la aprobación (o el rechazo) del contenido EXACTO que vio Daniel y aplica sus efectos. Devuelve su id."""
    if decision not in ("aprobada", "rechazada"):
        raise ValueError("decisión inválida")
    if hash_visto != obj.hash:
        auditoria.registrar(
            conn,
            "daniel",
            "approval.contenido_cambiado",
            f"{obj.tipo}/{obj.id}",
            {"visto": hash_visto, "actual": obj.hash},
        )
        raise ContenidoCambiado("el contenido cambió desde que lo abriste: revísalo de nuevo")
    with conn.transaction():
        aid = str(
            conn.execute(
                "INSERT INTO approval (created_by, objeto_tipo, objeto_id, content_hash, decision, comentario, "
                "totp_paso) VALUES ('daniel', %s, %s, %s, %s, %s, %s) RETURNING id",
                (obj.tipo, obj.id, obj.hash, decision, comentario[:2000] or None, totp_paso),
            ).fetchone()[0]  # type: ignore[index]
        )
        auditoria.registrar(
            conn,
            "daniel",
            "approval.decidida",
            f"approval/{aid}",
            {"tipo": obj.tipo, "objeto": obj.id, "hash": obj.hash, "decision": decision},
        )
        if decision == "aprobada" and obj.tipo in CONSUME_WEB:
            motivo = conn.execute(
                "SELECT approval_usar(%s, %s, %s, %s, 'daniel')", (aid, obj.tipo, obj.id, obj.hash)
            ).fetchone()[0]  # type: ignore[index]
            if motivo is not None:
                raise ContenidoCambiado(f"la aprobación no se pudo usar: {motivo}")
        _aplicar(conn, obj, decision, aid)
    return aid


def _aplicar(conn: psycopg.Connection, obj: Objeto, decision: str, aid: str) -> None:
    aprobada = decision == "aprobada"
    if obj.tipo == "organization_contact":
        if aprobada:
            # El canal confirmado antes para el mismo organismo y canal queda obsoleto (la fuente cambió).
            conn.execute(
                "UPDATE organization_contact SET estado = 'obsoleto' WHERE estado = 'confirmado' AND id <> %s "
                "AND (organization_id, canal) = "
                "(SELECT organization_id, canal FROM organization_contact WHERE id = %s)",
                (obj.id, obj.id),
            )
            conn.execute(
                "UPDATE organization_contact SET estado = 'confirmado', confirmed_at = now(), approval_id = %s "
                "WHERE id = %s",
                (aid, obj.id),
            )
        else:
            conn.execute("UPDATE organization_contact SET estado = 'descartado' WHERE id = %s", (obj.id,))
    elif obj.tipo == "finding":
        estado = "verificado" if aprobada else "rechazado"
        conn.execute("UPDATE finding SET estado = %s WHERE id = %s", (estado, obj.id))
    elif obj.tipo == "capture_release" and aprobada:
        conn.execute(
            "INSERT INTO capture_release (created_by, pii_scan_id, approval_id) VALUES ('daniel', %s, %s)",
            (obj.id, aid),
        )
    elif obj.tipo == "request":
        conn.execute("UPDATE request SET estado = %s WHERE id = %s", ("aprobada" if aprobada else "borrador", obj.id))
