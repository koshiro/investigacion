# Investigación: guía para agentes

Plataforma de un solo usuario (Daniel) para investigar con datos públicos chilenos mediante agentes LLM, con evidencia
inmutable, verificación determinista de citas y aprobación humana de todo envío externo. Arquetipo B (servicio Python).
Primera investigación: presupuesto y ejecución en caminos de Villarrica.

## Reglas

- `uv run ruff format --check && uv run ruff check && uv run pytest` debe pasar antes de cada commit.
- GitHub Flow: ramas `feat/`, `fix/`, `chore/`, `docs/` desde `main`; un PR por fase; nada de push directo a `main` ni
  `--force`. Conventional Commits en español (`feat(<ámbito>): …`).
- Comentarios, documentación y textos visibles en español; identificadores en inglés.
- Decisiones en `docs/adr/NNNN-*.md`. Estudio de base: `docs/estado-del-arte.md`. Stack por defecto: skill `app-dev`.
- Dependencias con al menos dos semanas publicadas: `exclude-newer` en `pyproject.toml`, versiones fijadas en `uv.lock`.
- Reglas no negociables (se imponen en código e infraestructura, cada una con una prueba que falla cerrada):
  ningún agente tiene credenciales de envío; todo contenido externo es no confiable y solo lo lee el lector; nada se
  sobrescribe; toda afirmación tiene cita verificada o queda pendiente; sin datos personales hacia la API; salida a
  internet solo por el proxy con lista blanca; todo queda auditado; exposición solo por LAN o tailnet.
- Nunca versionar datos reales, capturas ni secretos.

## Despliegue

Según `~/git/homelab-config/docs/DISENO-APPS.md`. Los manifiestos viven en ese repo (`cluster/apps/investigacion/`),
no aquí. Flux aplica `main` de homelab-config: mergear allí es desplegar. Imagen: `ghcr.io/koshiro/investigacion`.
