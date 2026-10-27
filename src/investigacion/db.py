"""Conexión y migraciones en SQL plano (archivos numerados en `migraciones/`, aplicados una sola vez y en orden)."""

from importlib import resources

import psycopg

TABLA = "schema_migrations"


def migraciones() -> list[tuple[str, str]]:
    archivos = sorted(
        (a for a in resources.files("investigacion.migraciones").iterdir() if a.name.endswith(".sql")),
        key=lambda a: a.name,
    )
    return [(a.name, a.read_text(encoding="utf-8")) for a in archivos]


def migrar(conn: psycopg.Connection) -> list[str]:
    """Aplica las migraciones pendientes con el rol dueño; devuelve los nombres aplicados."""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLA} (nombre text PRIMARY KEY, aplicada_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    hechas = {r[0] for r in conn.execute(f"SELECT nombre FROM {TABLA}")}  # noqa: S608
    aplicadas = []
    for nombre, sql in migraciones():
        if nombre in hechas:
            continue
        with conn.transaction():
            conn.execute(sql)  # type: ignore[arg-type]  # SQL de archivos del paquete, no de usuarios
            conn.execute(f"INSERT INTO {TABLA} (nombre) VALUES (%s)", (nombre,))  # noqa: S608
        aplicadas.append(nombre)
    conn.commit()
    return aplicadas
