# Traspaso a la fase 3

Fecha: 2026-10-29. Fase 2 (investigación documental) **desplegada**. La aceptación en vivo **espera a Daniel**: pedir el
plan, aprobarlo y confirmar los canales en la bandeja. Ningún agente corre sin esa aprobación, y eso es por diseño.

## Estado en una mirada

| Pieza | Estado | Dónde |
|-------|--------|-------|
| Esquema de la fase 2 (migraciones 0006–0009) | aplicado en vivo | `migraciones/`; ADR-0004 |
| Bandeja de aprobaciones (TOTP en cada decisión, hash, diff) | desplegada | `/aprobaciones` en web (NodePort 30960) |
| Worker DBOS (`dbos_worker`, versión `fase2-1`) | desplegado, cola `agentes` | `flujos.py`; Deployment `worker` |
| SearXNG | desplegado; **solo Bing responde** (ver hallazgos) | Deployment `searxng`; proxy con usuario `searxng` |
| Investigador, redactor, verificador | código y pruebas; **plan real probado** (29 pasos, USD 0,055, 0 residuos de datos personales) | `agentes.py` |
| Descubrimiento de canales | código y pruebas; en vivo bloqueado en 3 de 4 organismos (ver hallazgos) | `descubrimiento.py`, `flujo_descubrir` |
| Inventario de la lista blanca | código y pruebas; corre como tarea del plan | `flujo_inventario` |

Imagen en producción: ver `cluster/apps/investigacion/web.yml` en homelab-config. 268 pruebas en CI (Postgres real; los flujos con DBOS real y un login que solo
tiene `inv_worker`).

## Primera corrida real del plan (2026-10-29/30)

Plan de 30 tareas aprobado por Daniel: 29 hechas, 1 fallida. Costo total de la API: USD 0,43; `residuos_pii` = 0 en
todo lo enviado.

- **Inventario**: los 18 dominios de `capture` marcados con evidencia: 16 verificados (`www.villarrica.cl` resultó ser
  un **dominio en venta**), `www.portaltransparencia.cl` no accesible (403) y `www.gorearaucania.cl` no accesible (TLS).
- **Descubrimiento**: 0 canales (la búsqueda web no devolvió páginas oficiales y el Portal da 403). Corregido en #33:
  ahora sigue en código los enlaces de las páginas oficiales. Medido con las portadas reales: Vialidad → formulario del
  Portal `idOrg=1043` (candidato); Municipalidad → formulario sin id (revisar) + «Canales de Recepción de Solicitudes»
  y OIRS a seguir; SERVIU solo enlaza por goo.gl; GORE bloqueado por TLS.
- **Lector**: 19 fichas; una captura bloqueada por el gateway cortó la tarea entera. Causa real: el detector de
  teléfonos veía números dentro de UUID (0,7 % de los UUID; el `capture_id` va en la etiqueta del documento).
  Corregido en `redaccion-3` y los prompts usan etiquetas cortas (`c1`, `h1`).
- **Hallazgos**: 13 verificados, 2 rechazados, pero **poco pertinentes** (proyecto habitacional de Temuco, API de
  Mercado Público): las capturas eran portadas genéricas. Ahora el investigador solo propone hechos sobre caminos de
  la comuna y el verificador rechaza lo no pertinente. Falta capturar documentos de fondo (BIP filtrado por comuna,
  presupuestos, licitaciones): es el trabajo de la fase 4 y de un próximo plan.
- **Borradores**: 4 bien formados (piden información concreta y reutilizable), sin afirmaciones de contexto y sin
  canal confirmado (se rehacen al confirmar los canales).
- **Duplicados**: tres clics en «Proponer plan» encolaron tres planes; ahora hay un plan a la vez por investigación.

## Qué debe hacer Daniel (en orden)

0. TOTP autocompletado: los campos se llaman `totp` con `autocomplete="one-time-code"` y el usuario va en el mismo
   formulario. Hay que guardar el código TOTP en el gestor de contraseñas (1Password, Bitwarden o Apple Passwords:
   «agregar código de verificación» en la entrada de la web, con la URI `otpauth://` que imprime `crear-usuario`).
1. Entrar en la web → «Caminos de Villarrica» → **Proponer plan**. A los ~30 s aparece en **Aprobaciones**. Revisar
   el contenido exacto (y `problemas_de_permisos`) y aprobar con un código TOTP nuevo.
2. Seguir la ejecución en la misma página (tareas, estado, costo). Al terminar, aprobar en la bandeja los canales de
   transparencia propuestos (cada uno con su captura y cita), los hallazgos con citas en `revisar` y los borradores.
3. (Opcional) Secret `solicitante` con nombre y correo para notificaciones (README de los manifiestos). Sin él los
   borradores llevan marcadores.
4. Decidir lo de la sección «Decisiones abiertas».

## Criterios de aceptación de la fase 2

| Criterio | Estado | Evidencia |
|----------|--------|-----------|
| Cada fuente de la lista blanca: verificada, inexistente o no accesible | **Listo para correr** (tarea `source_inventory` del plan). Medido a mano el 29-oct: SERVIU OK; Portal 403; GORE falla TLS; portada de Vialidad corta la conexión | `test_inventario_marca_cada_dominio_con_evidencia`; `source_domain` |
| El plan pasa por aprobación; sin ella los agentes no se ejecutan | **Cumplido** (código + base) | `test_sin_aprobacion_no_corre_nada…`, `test_una_tarea_no_corre_sin_el_plan_aprobado` (trigger) |
| Descubrimiento y confirmación del canal de los 4 organismos, con captura y cita | **Parcial**: el flujo funciona con capturas reales; Daniel confirma. Barreras externas en 3 organismos (abajo) | `test_descubrimiento_propone_el_canal…`, `test_confirmar_un_canal…` |
| Borradores de las 4 solicitudes, cada afirmación ligada a un hallazgo verificado | **Listo para correr**; depende de hallazgos verificados | `test_el_borrador_no_envia_datos…`, `claim_exige_verificado` |
| Herramienta fuera del rol: se rechaza y se audita | **Cumplido** | `test_herramienta_fuera_del_rol_se_rechaza_y_queda_auditada` (5 casos); en el flujo real, `test_sin_aprobacion…` |
| Contenido externo (títulos de SearXNG incluidos) delimitado y etiquetado | **Cumplido** | `test_titulos_externos_llegan_delimitados…` |
| Nada sale hacia la API con datos personales (`llm_call`) | **Cumplido en vivo** para el plan real (`residuos_pii=0`); los datos del solicitante los agrega el código después | `llm_call`, `test_el_borrador_no_envia_datos_del_solicitante…` |

## Hallazgos del despliegue en vivo (no los mostraban las pruebas)

1. **El dominio municipal era otro**: el sitio oficial es `munivillarrica.cl`; la semilla usaba `villarrica.cl`
   (sin verificar qué es). Corregido en 0009 y en la lista del proxy. La ficha del Portal es `MU339`.
2. **Buscadores gratuitos desde una IP**: Brave responde 429 con captcha; DuckDuckGo pide CAPTCHA tras ~6 consultas
   (y falla con `site:`); Google y Qwant dan «access denied». Solo Bing responde, con resultados pobres para consultas
   institucionales. Medido el 29-oct.
3. **El Portal de Transparencia responde 403** al cliente de capture (igual que en la fase 1). La mayoría de los
   organismos remite su transparencia y su formulario de solicitudes al Portal (SERVIU: código `AP011`).
4. **`www.gorearaucania.cl` no presenta la cadena de certificados completa** (`CERTIFICATE_VERIFY_FAILED`).
5. **La portada de `vialidad.mop.gob.cl` corta la conexión** (sus PDF sí se descargaron en la fase 1).
6. **DBOS 3.2**: las colas se registran tras `launch()` (`DBOS.register_queue`) y la base de sistema se pasa como URL,
   no DSN. Lo encontró la prueba con DBOS real; la primera versión del CI se colgó porque el login de prueba no tenía
   clave por TCP.
7. **SearXNG baja las listas de ClearURLs al arrancar, sin proxy**: la NetworkPolicy lo corta (el aviso es esperado).

No se evadieron las barreras: ni User-Agent falso, ni TLS sin verificar, ni salida fuera del proxy.

## Decisiones abiertas (para Daniel)

- **Buscador**: ¿API de pago con clave (p. ej. Brave Search API) por el proxy, o descubrimiento solo por URLs
  conocidas y enlaces de páginas oficiales? Recomendación: URLs conocidas + seguir enlaces de páginas oficiales en
  código (requiere guardar los enlaces en la extracción HTML; hoy se descartan).
- **Portal 403**: ¿se registra una cuenta del Portal y se pide al CPLT un acceso para clientes automáticos, o Daniel
  sube capturas hechas con su navegador (falta un «subir captura» con hash en web)?
- **GORE TLS**: agregar a `capture` el certificado intermedio que falta (se mantiene la verificación) o tratarlo como
  «no accesible».
- **Ley 21.719** (retiro de capturas): pospuesto a esta fase por Daniel.
- **Feriados 2026–2027** y regla de cómputo de plazos.

## Fase 3 (solicitudes y plazos)

Dispatcher con la API de Gmail en `dry_run`, máquina de estados de solicitudes (`request` ya existe con
`borrador/en_revision/aprobada/descartada`; agregar `enviada…` separando estado y resultado como Froide), días
hábiles con `holiday`, alertas por Telegram. La aprobación de una solicitud ya queda en `approval` (tipo `request`,
sin usar): el dispatcher la consume con `approval_usar()` y compara el hash del mensaje exacto.

## Cómo operar y probar

- Pruebas: `INVESTIGACION_TEST_DATABASE_URL=… uv run pytest` (Postgres local: `scripts/pg-local.py`). Las pruebas
  migran con un dueño sin superusuario, como CNPG.
- Logs: `kubectl -n investigacion logs deploy/worker`; tareas y costos en la página de la investigación.
- Si cambia la secuencia de pasos de un flujo, subir `flujos.VERSION_FLUJOS` (los planes en espera se vuelven a pedir).
