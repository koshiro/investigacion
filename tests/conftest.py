import os
import uuid

import psycopg
import psycopg.conninfo
import pytest

from investigacion import db

URL_ADMIN = os.environ.get("INVESTIGACION_TEST_DATABASE_URL")


def _con_base(url: str, base: str) -> str:
    """Misma conexión de superusuario apuntando a otra base (URL de socket o de red)."""
    return str(psycopg.conninfo.make_conninfo(url, dbname=base))


@pytest.fixture
def pg():
    """Base nueva y migrada por prueba. Devuelve una función `conectar(rol=None)` y la conexión de superusuario."""
    if not URL_ADMIN:
        if os.environ.get("CI"):
            pytest.fail(
                "En CI hace falta INVESTIGACION_TEST_DATABASE_URL: sin Postgres las pruebas de seguridad no corren"
            )
        pytest.skip("sin INVESTIGACION_TEST_DATABASE_URL (ver scripts/pg-local.py)")
    nombre = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(URL_ADMIN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{nombre}"')  # type: ignore[arg-type]
    url = _con_base(URL_ADMIN, nombre)
    conexiones: list[psycopg.Connection] = []

    def conectar(rol: str | None = None) -> psycopg.Connection:
        conn = psycopg.connect(url, autocommit=True)
        conexiones.append(conn)
        if rol:
            conn.execute(f"SET ROLE {rol}")  # type: ignore[arg-type]
        return conn

    su = conectar()
    db.migrar(su)
    su.autocommit = True
    yield type("PG", (), {"conectar": staticmethod(conectar), "su": su, "url": url})
    for c in conexiones:
        c.close()
    with psycopg.connect(URL_ADMIN, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')  # type: ignore[arg-type]
