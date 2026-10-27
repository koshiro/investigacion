# investigacion

Plataforma de investigación con datos públicos chilenos: evidencia inmutable, citas verificadas y aprobación humana.

Estado: fase 0 (infraestructura base y prototipo de flujos durables). Guía para agentes: `CLAUDE.md`.

```bash
uv sync
uv run pytest
INVESTIGACION_DATABASE_URL=sqlite:///dbos.sqlite uv run investigacion prototipo
```
