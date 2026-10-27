# Estudio del estado del arte sobre la especificación del MVP

Fecha: 2026-10-27. Revisa la «Especificación del MVP: plataforma de investigación» (27-oct-2026) contra la literatura
y el código de proyectos comparables, y contra el cluster real. Conclusión: **la especificación es sólida y se
mantiene**; estos son los ajustes. Ninguno relaja una regla no negociable; varios las endurecen.

## Método

- Código leído (clonado con `--depth 1`, sin ejecutar): Froide `c6f88a6` (MIT), DBOS Transact Python `2873b8c`
  (MIT; el paquete `dbos-enterprise` es propietario), Presidio `211afc3` (MIT), Procrastinate (MIT).
- Documentación primaria: CloudNativePG 1.30 (`installation_upgrade.md`, notas 1.28–1.30), imágenes
  `cloudnative-pg/postgis-containers` (Dockerfile), Cloudflare R2 Bucket Locks, Google OAuth (scopes restringidos),
  API de Claude (citations, structured outputs, precios), Beurer-Kellner et al. 2025 «Design Patterns for Securing
  LLM Agents against Prompt Injections» (arXiv 2506.08837), firma de WACZ (Harvard LIL / Webrecorder).
- Cluster: k3s 1.35.9, NetworkPolicy aplicada por kube-router (408 reglas en node-2), CNPG 1.30.1 desde hoy,
  pools ZFS de pve, timers de respaldo.

## Lo que ya está alineado con el estado del arte

| Regla de la especificación | Respaldo |
|---|---|
| Lector sin herramientas que ve el contenido externo; el resto ve fichas | Patrón *dual LLM* (arXiv 2506.08837 §3) |
| El investigador propone un plan y Daniel lo aprueba antes de ejecutar | Patrón *plan-then-execute*: el contenido externo no cambia qué herramientas se llaman |
| Aprobación ligada al hash del mensaje exacto, de un solo uso | Igual que la plantilla de la skill app-dev (regla 5) |
| Verificación determinista de citas; el LLM verificador no cambia `verified` | Anti-invención por capas deterministas (skill app-dev, `references/llm.md`) |
| Postgres como centro (datos, flujos, vectores, texto completo) | Regla 1 de la skill app-dev |

Riesgo residual que la literatura señala y la especificación debe asumir explícitamente: los patrones impiden que el
contenido externo **elija** acciones, pero no que **influya en sus argumentos** (qué URL capturar, qué buscar, qué
dice un borrador). Por eso se mantiene la aprobación humana de todo envío y exportación.

## Ajustes recomendados

### Seguridad

1. **Separar `capture` en `capture` y `extract`.** `capture` descarga (sale por el proxy) y guarda bytes con hash.
   `extract` corre docling, Tesseract y Presidio **sin ninguna salida de red**: un exploit en un parser de PDF o de
   planilla no puede exfiltrar nada. Costo: un Deployment más.
2. **Proxy con autenticación por cliente.** Squid no puede distinguir clientes por IP porque las de los pods cambian.
   Cada cliente (`capture`, `llm-gateway`, `dispatcher`, `searxng`) usa su usuario en `HTTPS_PROXY` y Squid aplica
   la lista de dominios por usuario. El proxy no puede salir a rangos privados (NetworkPolicy), lo que corta SSRF y DNS
   rebinding hacia la LAN.
3. **Definir contra qué texto se verifican las citas.** El lector ve texto redactado (`[PERSONA_3]`) y la cita debe
   existir en la captura. Decisión: la cita se verifica contra la versión redactada de la extracción y `pii_scan`
   guarda el mapa de desplazamientos redactado → original para mostrar la cita en la captura.
4. **Anclar fuera del cluster la auditoría y la evidencia.** Una cadena de hashes la puede reescribir entera quien
   controle la base. El hash final de cada día va a un bucket R2 con *Bucket Lock* (retención que impide borrar o
   sobrescribir) y se sella con OpenTimestamps. Cada captura nueva también se copia a ese bucket: copia fuera de casa
   inmediata e inmutable, que además cubre parte del prerrequisito de redundancia.
5. **Los títulos de SearXNG son contenido externo.** Van delimitados con su procedencia como cualquier otro, y la
   consulta de búsqueda es un canal de salida: largo máximo y registro en auditoría.

### Infraestructura (verificado en el cluster)

1. **Imagen de la base:** `ghcr.io/cloudnative-pg/postgis:17.11-3.6.4-<fecha>-standard-trixie`. Se construye sobre
   `postgresql:17-standard-trixie`, que ya trae pgvector (el cluster compartido lo usa), y agrega PostGIS y pgrouting.
   No hay que compilar nada; cierra la decisión técnica abierta.
2. **Respaldo con barman-cloud a Garage** (base diaria + WAL continuo, PITR de 30 días), como `homelab-postgres`, en
   vez de un dump diario. Garage queda en `nvme/k8s/garage`, que entra en sanoid, syncoid y restic a R2.
3. **NetworkPolicy funciona** (kube-router activo): se puede confiar en la denegación por defecto.
4. **CNPG**: estaba en 1.27.0 sin soporte; subido a 1.30.1 el 2026-10-27 sin reiniciar Postgres (homelab-config
   #159–#162).
5. **Prerrequisitos**: «restic a R2» ya corre a diario (`restic-offsite.timer`). El pool `nvme` sigue con un disco,
   pero syncoid lo replica cada hora al pool `replica`; la pérdida máxima es una hora, y con la copia inmediata a R2
   (seguridad 4) la evidencia queda cubierta.

### Ley 20.285 y correo

1. **Plazo de reclamo ante el CPLT.** El artículo 24 dice «quince días» sin «hábiles»; la especificación asume
   hábiles. Confirmar contra el texto de BCN antes de la fase 3; mientras tanto, las alertas usan el plazo más corto.
2. **Separar estado del proceso y resultado**, como Froide (`froide/foirequest/models/request.py:190-227`: `Status`
   con `awaiting_response`, `awaiting_classification`, `resolved`…, y `Resolution` con `successful`,
   `partially_successful`, `refused`, `not_held`…). Evita estados mezclados como `respondida` → `reclamable`.
3. **Una dirección de respuesta por solicitud.** Froide asocia respuestas por una dirección secreta por solicitud
   (`request.py:347`, búsqueda en la línea 46). Con Gmail: `cuenta+<id corto>@gmail.com`. Es más robusto que
   `In-Reply-To`, que muchos sistemas de oficina de partes no conservan. Si un organismo exige el Portal de
   Transparencia, registrar la cuenta del portal con esa misma dirección.
4. **El RUT de una persona natural es su RUN.** Un contratista persona natural es dato personal; la regla «el RUT de
   empresas puede quedar» necesita un criterio en código (p. ej. persona jurídica según rango del RUT, verificado contra
   el SII o Mercado Público) y, en duda, se redacta.
5. **Gmail**: `gmail.modify` es scope restringido; el uso personal es una excepción documentada de la verificación y
   en estado «En producción» el refresh token no vence a los 7 días. Validar al inicio de la fase 3, como ya dice la
   especificación.

### Stack (alinear con la skill app-dev)

1. **Python 3.14** en vez de 3.12: spaCy 3.8.18, torch 2.14 y psycopg 3.3 publican wheels cp314; presidio-analyzer
   admite `<3.15`.
2. **psycopg + SQL plano** con `GRANT` explícitos por rol en las migraciones, en vez de SQLAlchemy/Alembic; `ty` en
   vez de mypy. DBOS usa SQLAlchemy internamente para su esquema; la app no lo necesita.
3. **DBOS** (MIT) calza con lo que la plataforma necesita: `DBOS.recv(topic, timeout)` para esperar aprobaciones,
   `DBOS.sleep` durable, scheduler y colas. No usar `dbos-enterprise`/Conductor (propietario; además saldría a
   `cloud.dbos.dev`). Sin Conductor no hace llamadas de red. Se valida con el prototipo de la fase 0 (ADR-0002).
4. **Lector con la API de Citations de Claude.** Con documentos de texto plano devuelve `cited_text` y desplazamientos
   de caracteres exactos, pero no admite structured outputs. Comparar en un eval: (a) JSON validado + verificador,
   (b) citations + armado de la ficha en código. El verificador determinista se queda en ambos casos.
5. **Datos personales**: Presidio trae `GLiNERRecognizer` (`urchade/gliner_multi_pii-v1`, multilingüe), mejor que
   solo spaCy para nombres en español. El reconocedor de RUN con dígito verificador es propio (Presidio no trae Chile).
   torch en su variante CPU para que la imagen de `extract` no pese 8 GB.

### Fuentes que conviene agregar al catálogo (verificar en la fase 2)

- Red vial nacional de Vialidad (IDE del MOP) con geometría y rol de cada camino: base de H4 (caminos sin responsable).
- SUBDERE (PMU y PMB): financia pavimentación municipal y no está entre los organismos.
- Datos de contratación de ChileCompra en formato OCDS (Open Contracting Data Standard), si siguen publicados.

## Qué cambia en la fase 0

- Cluster `investigacion-pg` con la imagen PostGIS de arriba, una instancia, barman-cloud a Garage.
- `egress-proxy` con usuarios por cliente; la NetworkPolicy del proxy excluye rangos privados.
- Imagen en Python 3.14 con `exclude-newer` de uv para cumplir la regla de dos semanas.
- El prototipo DBOS valida ADR-0002.
