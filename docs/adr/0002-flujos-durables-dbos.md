# ADR-0002: Flujos durables con DBOS Transact

Estado: Propuesto (se acepta al pasar el prototipo de la fase 0) · Fecha: 2026-10-27

## Contexto

Los flujos esperan días (aprobación de Daniel, 20 días hábiles de plazo, respuesta de un organismo) y encadenan
pasos caros (llamadas a la API, capturas). Un reinicio del pod no puede repetir pasos ya hechos ni perder esperas.

## Decisión

DBOS Transact (MIT) sobre la misma base Postgres. Uso: `@DBOS.workflow` y `@DBOS.step`; `DBOS.recv(topic, timeout)`
para aprobaciones; `DBOS.sleep` para esperas; colas para limitar concurrencia hacia la API. Sin Conductor ni
`dbos-enterprise` (propietario y con salida a `cloud.dbos.dev`).

Criterio de aceptación (fase 0): el flujo `prototipo-fase0` (10 pasos, 30 s de espera durable entre pasos) retoma tras
`kubectl rollout restart` desde el último paso completo, sin repetir pasos: en `dbos.operation_outputs` cada paso
aparece una vez y los pasos posteriores al reinicio llevan el nombre del pod nuevo.

## Alternativas consideradas

| Opción | Por qué no |
|--------|-----------|
| Procrastinate (default de la casa) | Cola de trabajos sin pasos reanudables: habría que escribir la máquina de estados y los checkpoints |
| Temporal / Hatchet | Servidor aparte que operar y respaldar |

## Consecuencias

- DBOS crea su propio esquema (`dbos`) en la base; el rol del worker necesita escribir ahí.
- DBOS fija la versión del flujo por hash del código: un despliegue con cambios en un flujo no retoma flujos viejos
  con código nuevo. Revisar la estrategia de versiones antes de la fase 3.
