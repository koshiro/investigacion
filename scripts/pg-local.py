"""Postgres local para las pruebas (sin Docker).

Uso: `uv run --no-project --python 3.12 --with pgserver python scripts/pg-local.py`.

Imprime la URL de superusuario y queda vivo hasta Ctrl+C. Exportarla como INVESTIGACION_TEST_DATABASE_URL.
pgserver trae PostgreSQL 16 con pgvector (sin PostGIS: las pruebas no tocan geodatos).
"""

import threading

import pgserver

servidor = pgserver.get_server("/tmp/investigacion-pg-test")  # noqa: S108
print(servidor.get_uri(), flush=True)
threading.Event().wait()
