"""Registro de auditoría encadenado por hash (solo inserción).

Agregar: `registrar()` llama a la función SQL `audit_append` (única vía; los roles de servicio no tienen INSERT).
Validar: `verificar()` recalcula la cadena completa en Python, con una implementación independiente de la de SQL.
"""

import hashlib
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

GENESIS = "0" * 64


def registrar(conn: psycopg.Connection, actor: str, accion: str, objeto: str, datos: dict | None = None) -> int:
    fila = conn.execute("SELECT audit_append(%s, %s, %s, %s)", (actor, accion, objeto, Jsonb(datos or {}))).fetchone()
    assert fila is not None
    return fila[0]


def calcular_hash(prev: str, seq: int, ts_utc: str, actor: str, accion: str, objeto: str, datos_texto: str) -> str:
    base = "|".join([prev, str(seq), ts_utc, actor, accion, objeto, datos_texto])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResultadoCadena:
    ok: bool
    filas: int
    hash_final: str
    primera_rota: int | None = None
    motivo: str = ""


def verificar(conn: psycopg.Connection) -> ResultadoCadena:
    """Recorre la cadena de punta a punta. Detecta cambios de contenido, filas borradas, insertadas o reordenadas."""
    consulta = (
        "SELECT seq, to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'), actor, accion, objeto, "
        "datos::text, prev_hash, hash FROM audit_log ORDER BY seq"
    )
    prev = GENESIS
    esperado = 1
    n = 0
    for seq, ts, actor, accion, objeto, datos, prev_hash, hash_ in conn.execute(consulta):
        if seq != esperado:
            return ResultadoCadena(False, n, prev, seq, f"secuencia {seq}, se esperaba {esperado} (fila faltante)")
        if prev_hash != prev:
            return ResultadoCadena(False, n, prev, seq, "prev_hash no coincide con la fila anterior")
        if calcular_hash(prev, seq, ts, actor, accion, objeto, datos) != hash_:
            return ResultadoCadena(False, n, prev, seq, "el contenido de la fila no coincide con su hash")
        prev = hash_
        esperado += 1
        n += 1
    return ResultadoCadena(True, n, prev)
