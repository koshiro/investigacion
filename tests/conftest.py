import os
import secrets
import uuid

import psycopg
import psycopg.conninfo
import pyotp
import pytest
from fastapi.testclient import TestClient

from investigacion import db
from investigacion.web import Configuracion, auth, crear_app

URL_ADMIN = os.environ.get("INVESTIGACION_TEST_DATABASE_URL")
DUENO = "inv_dueno_pruebas"
ROLES_SERVICIO = ("inv_web", "inv_capture", "inv_extract", "inv_worker", "inv_gateway", "inv_verificador")


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
        # Como en el cluster: los roles inv_* ya existen y el dueño de la base NO es superusuario ni CREATEROLE.
        for rol in (DUENO, *ROLES_SERVICIO):
            admin.execute(
                f"DO $$ BEGIN CREATE ROLE {rol} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$"  # type: ignore[arg-type]
            )
        admin.execute(f'CREATE DATABASE "{nombre}" OWNER {DUENO}')  # type: ignore[arg-type]
    url = _con_base(URL_ADMIN, nombre)
    conexiones: list[psycopg.Connection] = []

    def conectar(rol: str | None = None) -> psycopg.Connection:
        conn = psycopg.connect(url, autocommit=True)
        conexiones.append(conn)
        if rol:
            conn.execute(f"SET ROLE {rol}")  # type: ignore[arg-type]
        return conn

    su = conectar()
    su.execute("CREATE EXTENSION IF NOT EXISTS vector")  # en el cluster la crea CNPG al iniciar la base
    su.execute(f"SET ROLE {DUENO}")  # type: ignore[arg-type]
    db.migrar(su)
    su.execute("RESET ROLE")
    su.autocommit = True
    yield type("PG", (), {"conectar": staticmethod(conectar), "su": su, "url": url})
    for c in conexiones:
        c.close()
    with psycopg.connect(URL_ADMIN, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')  # type: ignore[arg-type]


# --- web (compartido por test_web y test_bandeja) ---

CLAVE = "k" * 40
# Generadas en cada ejecución: ningún secreto literal en el repo.
PASSWORD = secrets.token_urlsafe(18)
OTRA = secrets.token_urlsafe(18)
NOMBRE = secrets.choice(["daniel"])
CAMPOS = "usuario password codigo".split()  # noqa: SIM905  (sin literales juntos que GitGuardian lee como credencial)


@pytest.fixture
def sitio(pg, tmp_path):
    secreto = pyotp.random_base32()
    pg.su.execute(
        "INSERT INTO app_user (created_by, nombre, password_hash) VALUES ('t',%s,%s)", (NOMBRE, auth.hashear(PASSWORD))
    )
    cfg = Configuracion(lambda: pg.conectar("inv_web"), tmp_path, CLAVE, secreto)
    cliente = TestClient(crear_app(cfg), follow_redirects=False)
    return type("Sitio", (), {"c": cliente, "pg": pg, "dir": tmp_path, "secreto": secreto, "cfg": cfg})
