# Imagen base de la plataforma: el Deployment elige el proceso con `args:` (hoy solo `prototipo`).
# Contrato del cluster: DISENO-APPS §2 (no-root 1000, readOnlyRootFilesystem, logs a stdout).
FROM ghcr.io/astral-sh/uv:0.13-python3.14-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencias primero (capa cacheable), luego el código.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie
# Agregar aquí binarios del sistema que la app necesite (p. ej. poppler-utils para pdftotext).
RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -M -d /nonexistent -s /usr/sbin/nologin app
COPY --from=build --chown=1000:1000 /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 TZ=America/Santiago
WORKDIR /app
USER 1000:1000
ENTRYPOINT ["investigacion"]
CMD ["prototipo"]
