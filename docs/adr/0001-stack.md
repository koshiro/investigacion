# ADR-0001: Stack de la plataforma

Estado: Aceptado · Fecha: 2026-10-27

## Contexto

Aplicación de un solo usuario en el cluster k3s del homelab (arquetipo B de la skill app-dev: servicio Python con
trabajos en segundo plano y datos relacionales). Las piezas reutilizables (docling, Presidio, spaCy, GLiNER,
Playwright, SDK de Claude) están en Python. Detalle de la evaluación: `docs/estado-del-arte.md`.

## Decisión

Valores por defecto de `app-dev/references/stack.md`, con estas desviaciones:

| Capa | Elegido | Desviación |
|------|---------|-----------|
| Lenguaje | Python 3.14, uv con `exclude-newer` (dos semanas) | — |
| Web | FastAPI + Jinja + HTMX | — |
| Postgres | psycopg 3.3 + SQL plano, migraciones numeradas con `GRANT` por rol | — |
| Flujos durables | **DBOS Transact** en vez de Procrastinate | Esperas de días (aprobaciones, plazos) y pasos reanudables; ADR-0002 |
| Base | Cluster CNPG propio `investigacion-pg` (PostGIS + pgvector) | No cabe en `homelab-postgres`: PostGIS exige otra imagen y la auditoría pide roles propios |
| LLM | SDK `anthropic`, tool use propio, modelo por rol en variables de entorno | — |
| Documentos | docling + Tesseract (es) + openpyxl/pandas | — |
| Datos personales | Presidio + GLiNER + reconocedor propio de RUN | — |
| Tipos | ty (aviso en CI) | La especificación pedía mypy |

## Alternativas consideradas

| Opción | Por qué no |
|--------|-----------|
| SQLAlchemy + Alembic (especificación) | El stack de la casa usa SQL plano; los `GRANT` por rol se leen mejor en SQL |
| Temporal | Servidor aparte; descartado en la especificación |
| Aleph, Datashare, Froide completos | Plataformas multiusuario y pesadas; se toma de Froide solo el modelo de estados (MIT) |

## Consecuencias

- Una imagen liviana (`web`, `worker`, `llm-gateway`, `dispatcher`) y otra pesada para `capture`/`extract`
  (Chromium, docling, torch CPU).
- Revisar si DBOS no pasa el prototipo de la fase 0 (ADR-0002).

## Decisiones de Daniel (2026-10-27)

- Acceso a `web`: NodePort **30960** (serie 3096x reservada para esta plataforma; LAN y tailnet, sin Ingress).
- Límite de gasto en la API de Claude: lo fija Daniel en la consola de Anthropic. `llm-gateway` registra tokens y
  costo por llamada; un tope propio en la plataforma queda opcional.
- Cuenta Gmail y proyecto de Google Cloud: los prepara Daniel al llegar a la fase 3.
- Plazos: según el texto legal (`docs/plazos-ley-20285.md`).
