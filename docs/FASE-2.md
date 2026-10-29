# Traspaso a la fase 2

Fecha: 2026-10-28. Fase 1 entregada y desplegada; falta la revisión de Daniel (la fase solo se cierra con su visto bueno).

## Estado en una mirada

| Pieza | Estado | Dónde |
|-------|--------|-------|
| Esquema SQL, roles, auditoría | desplegado | `src/investigacion/migraciones/0001–0005`; manifiestos en `homelab-config/cluster/apps/investigacion/` |
| `web` (login argon2 + TOTP) | desplegado, **sin usuario creado** | NodePort 30960 |
| `capture` | desplegado y probado con fuentes reales | cola `capture_job` |
| `extract` | desplegado; ver «Hallazgos» (rendimiento) | imagen `investigacion-extract` |
| `llm-gateway` | desplegado en **0 réplicas** | falta Secret `anthropic-api` |
| `lector` | CronJob suspendido | `investigacion lector --pendientes` |
| Eval Citations vs JSON | código listo (`eval_lector.py`), **sin ejecutar contra la API** | `investigacion eval-lector` |

## Qué debe hacer Daniel (en orden)

1. Crear el usuario: receta en `homelab-config/cluster/apps/investigacion/README.md` (contraseña ≥ 14 caracteres; el
   TOTP es el del Secret `web-secreto`). Entrar a `http://<nodo>:30960`.
2. Poner el límite de gasto en la consola de Anthropic y crear el Secret `anthropic-api` (`ANTHROPIC_API_KEY`) como
   `anthropic-api.sops.yaml` en `solo-flux/`; subir `llm-gateway` a 1 réplica por PR.
3. Correr `investigacion eval-lector` (cuesta centavos) y decidir entre (a) JSON + verificador y (b) Citations.
4. Revisar los criterios de aceptación de abajo y cerrar la fase.

## Criterios de aceptación de la fase 1

| Criterio | Resultado | Evidencia |
|----------|-----------|-----------|
| Capturar el CSV del BIP y 10 PDF reales; el hash recalculado coincide | CSV `inversiones_2027.csv` (6,5 MB) y 10 PDF de SERVIU Araucanía y Vialidad capturados por el proxy; `investigacion verificar-capturas` sale 0 | `capture` (12 trabajos, 11 hechos, 1 `403` esperado) |
| Cadena de auditoría válida de punta a punta; modificar una captura se detecta | Válida (24 entradas al verificar); archivo alterado en vivo → «el contenido del archivo no coincide con su hash» (exit 1); restaurado → exit 0 | `tests/test_auditoria.py`, `tests/test_capture.py` |
| Verificador: 20 citas verdaderas aceptadas, 20 inventadas o alteradas rechazadas | 20/20 y 20/20 (conjunto fijo) | `tests/citas_fijas.py`, `tests/test_verificador.py` |
| Ningún RUN de prueba llega a la API (verificado en `llm_call`) | Probado con un doble de la API que guarda todo lo enviado y con un fallo simulado de la redacción (bloquea y no llama). **No hay llamadas reales todavía** (sin clave) | `tests/test_gateway.py` |
| Una inyección en un PDF no cambia el comportamiento del lector | Probado de forma **estructural** con un modelo simulado que obedece (3 variantes): sin herramientas, esquema `extra=forbid`, ficha como único efecto. **Falta la prueba de comportamiento con el modelo real** (caso `inyeccion` del eval) | `tests/test_lector.py` |

## Desviaciones respecto de la especificación (decididas, a confirmar)

- **DBOS no se usa todavía.** El prototipo de la fase 0 validó el reinicio, pero en la fase 1 todo es cola en Postgres
  (`capture_job`, extracción por consulta) o un trabajo corto (`lector`). DBOS entra con la espera de aprobaciones (fase
  2/3). ADR-0002 sigue vigente.
- **`extract` y `capture` son servicios separados** (ajuste del estado del arte); `extract` solo habla con Postgres.
- **Datos tabulares (CSV) se redactan sin el modelo de nombres** (`pii_scan.version = redaccion-1-determinista`):
  GLiNER rinde ~0,3 KB/s por CPU y el CSV del BIP tardaría horas. Cualquier CSV con nombres de personas naturales
  quedaría con los nombres. **Decisión pendiente**: columnas a revisar por fuente, o un modelo más rápido.
- **OCR solo donde falta texto** (páginas con < 80 caracteres): docling por defecto aplica OCR a cada imagen
  incrustada. Las infografías con poco texto pueden perder el texto de sus imágenes.
- **Nombres de autoridades**: el modelo redacta también a autoridades en ejercicio (la especificación permite
  dejarlas). Quedó conservador: ante la duda se redacta.
- **El lector no está dentro de un flujo DBOS ni tiene botón en la web**; se lanza con el CronJob `lector`.

## Hallazgos que condicionan la fase 2

- **Una cita literal del texto inyectado sí verifica.** El verificador prueba que la cita existe en la captura, no que
  sea cierta ni inofensiva. El investigador (fase 2) verá fichas con hechos que vienen de un documento hostil: debe
  tratarlas como «lo que dice el documento», y toda acción sigue pasando por aprobación humana.
- **Ley 21.719 (vigente desde el 1-dic-2026)**: `capture`, `extraction`, `pii_scan`, `card` y `audit_log` son
  inmutables por diseño, lo que choca con un eventual derecho de supresión. Hace falta un mecanismo de **tombstone**
  (marcar una captura como retirada, ocultar texto y archivo, conservar el hash) y una política de retención. Sin
  decidir.
- **Rol de `capture` sin SELECT sobre la auditoría**: la verificación de integridad se ejecuta desde `web`
  (`kubectl exec deploy/web -c web -- investigacion verificar-capturas`).
- **Imagen de `extract`**: ~13 min de CI en cada PR (se construye siempre) y ~2 GiB de RAM en régimen; el nodo
  `k3s-node-1` está al ~84 % de memoria. Revisar si conviene un nodo con más margen.
- **GitGuardian re-escanea todos los commits de un PR**: no escribir pares `"usuario"`/`"password"` literales ni
  siquiera en pruebas (ver `casos.md` de la skill app-dev).

## Qué sigue (fase 2: investigación documental)

Del documento de especificación: agentes `investigador`, `redactor` y `verificador`; `searxng`; bandeja de
aprobaciones; semillas de Villarrica; inventario de fuentes. Puntos de partida concretos:

1. Tabla `approval` y la bandeja en `web` (aprobación de uso único ligada a `content_hash`, TOTP en cada aprobación).
   El rol del worker no debe poder escribirla.
2. Mover el lector a un flujo DBOS (`DBOS.recv` para aprobaciones; el worker necesita escribir el esquema `dbos`).
3. Tablas faltantes: `organization`, `organization_contact`, `hypothesis`, `finding`, `task`, `holiday`.
4. Copia inmutable de auditoría y capturas a R2 con Bucket Lock (ajuste de seguridad 4 del estado del arte) y
   OpenTimestamps; alertas en vmalert (cadena rota, captura alterada, gasto) y `/metrics`.
5. Dominios pendientes de la lista blanca (portaltransparencia.cl, bcn.cl, gorearaucania.cl) siguen sin responder a
   clientes automáticos.
6. Pasar de los archivos `0001–0005` a la convención de nuevas migraciones (un archivo por cambio; nunca editar uno
   aplicado).

## Cómo operar y probar

- Pruebas: `INVESTIGACION_TEST_DATABASE_URL=… uv run pytest` (Postgres local sin Docker: `scripts/pg-local.py`).
  Con el extra `extract` (`UV_PROJECT_ENVIRONMENT=/tmp/venv-extract uv run --python 3.14 --extra extract --with fpdf2
  pytest`) corren también docling y GLiNER; sin él se saltan.
- Despliegue: subir el mismo `sha-<7>` en `web/capture/extract/llm-gateway/lector.yml` de `homelab-config`.
- Skills actualizadas: `app-dev` (stack, gateway, caso) y `homelab` (trampas y receta).
