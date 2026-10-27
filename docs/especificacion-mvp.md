# Especificación del MVP: plataforma de investigación

> Copia de la especificación original (27-oct-2026). Ajustes acordados: `docs/estado-del-arte.md`, `docs/adr/`, `docs/plazos-ley-20285.md`. Si se contradicen, mandan los ajustes.

Oct 27, 2026 · @Daniel

## Objetivo y alcance

Construir en el homelab de Daniel una aplicación web de un solo usuario que gestione investigaciones con datos públicos chilenos mediante agentes LLM, con evidencia inmutable, verificación de citas y aprobación humana obligatoria para todo envío externo. El MVP se valida con una investigación real: **presupuesto y ejecución en caminos urbanos y rurales de Villarrica** (Municipalidad de Villarrica, SERVIU Araucanía, Gobierno Regional de La Araucanía y Dirección de Vialidad del MOP).

Documentos de contexto: [evaluación crítica](https://claude.ai/artifact/SzMn1u9MwotcLW1PpB5TmW), [plan de investigación de Villarrica](https://claude.ai/artifact/6MpKbUnD6pQpaox4yVnpAK) y [entorno del homelab](https://claude.ai/artifact/QWy3zKLUVc4tADWE2J6a2X). Si este documento y el del homelab se contradicen en una regla de operación del clúster, manda el del homelab.

**Dentro del MVP**

- Gestión de una investigación: pregunta, hipótesis, organismos, fuentes, hallazgos.
- Captura de fuentes web y archivos con hash, fecha e inmutabilidad.
- Extracción de PDFs y planillas, detección y redacción de datos personales.
- Cuatro agentes: lector, investigador, redactor, verificador.
- Verificación determinista de citas literales.
- Solicitudes de acceso a la información (Ley 20.285): borrador, aprobación, envío por correo, recepción de respuestas y cálculo de plazos en días hábiles.
- Base de inversión vial de Villarrica (código reproducible) y catastro del estado de caminos con fotos georreferenciadas.
- Exportación de productos (Markdown/PDF/CSV) para enviar a terceros.
- Registro de auditoría encadenado.

**Fuera del MVP**

- Varios usuarios o acceso de terceros (periodistas y abogados reciben exportaciones, nunca acceso).
- Publicación automática en internet.
- Reclamos (amparos) automáticos ante el Consejo para la Transparencia: la plataforma avisa el plazo, Daniel lo presenta.
- Repositorio versionado de normas y verificación jurídica temporal (se agrega después; el MVP solo necesita los plazos de la Ley 20.285).
- El estudio de licencias de conducir.
- Modelos estadísticos avanzados.

## Reglas no negociables

Estas reglas se imponen en código e infraestructura, nunca solo en prompts. Cada una debe tener al menos una prueba automatizada que demuestre que falla cerrada.

1. **Ningún agente tiene credenciales de envío.** Solo el servicio `dispatcher` tiene la credencial de Gmail. Envía únicamente si existe una aprobación de Daniel cuyo `content_hash` coincide con el SHA-256 del mensaje exacto (destinatario, asunto, cuerpo y adjuntos) y que no se haya usado antes.
2. **Todo contenido externo es no confiable.** Páginas, PDFs, planillas y correos recibidos solo los procesa el agente lector, que no tiene herramientas. Los demás agentes ven fichas estructuradas, nunca el texto crudo. En todo prompt, el contenido externo va delimitado y etiquetado con su `capture_id`.
3. **Nada se sobrescribe.** Las capturas se guardan direccionadas por hash y no se modifican ni se borran desde la aplicación. Toda transformación crea un registro nuevo con referencia al original.
4. **Toda afirmación tiene evidencia o queda pendiente.** Un hallazgo solo puede pasar a `verificado` si cada cita existe literalmente en su captura (verificación determinista). Si no, queda `pendiente` y no puede exportarse como hecho.
5. **Sin datos personales hacia la API.** Antes de cualquier llamada a la API de Claude, el texto pasa por la redacción de datos personales (RUN, nombres de personas naturales, direcciones, teléfonos, correos de particulares). Los nombres de autoridades en ejercicio de su cargo y los RUT de empresas pueden quedar.
6. **Salida a internet solo por proxy con lista blanca.** Los pods de agentes no tienen salida directa. La API de Claude solo es alcanzable desde el `llm-gateway`.
7. **Todo queda auditado.** Cada acción de agentes, de Daniel y de servicios se registra en un log de solo inserción encadenado por hash.
8. **Exposición solo por LAN o tailnet.** No crear ningún Ingress bajo `*.danielriquelme.cl`: el túnel comodín de Cloudflare lo haría público.

## Arquitectura en el homelab

Todo vive en un namespace nuevo, `investigacion`, desplegado por Flux desde `cluster/apps/investigacion` del repo `homelab-config`, con imágenes en `ghcr.io/koshiro/investigacion-*` y `nodeSelector: kubernetes.io/arch=amd64` (el master es una Raspberry Pi arm64).

&#91;embedded content: componentes del MVP en el namespace investigacion\]

| Workload | Tipo | Función | Red permitida | Secretos |
| --- | --- | --- | --- | --- |
| `web` | Deployment | Interfaz y API: investigaciones, evidencia, bandeja de aprobaciones, exportaciones | Entrada: NodePort (confirmar puerto libre con `hl.py ports`). Salida: Postgres | Clave de sesión, secreto TOTP |
| `worker` | Deployment | Flujos durables (DBOS) y los cuatro agentes | Postgres, `llm-gateway`, `capture` | Ninguno externo |
| `llm-gateway` | Deployment | Único cliente de la API de Claude y de Ollama; redacción previa, límite de gasto, registro de cada llamada | Postgres, `egress-proxy` (solo `api.anthropic.com`), servicio interno de Ollama | `ANTHROPIC_API_KEY` |
| `capture` | Deployment | Descarga URLs (HTTP y Chromium para portales con JavaScript), calcula hash, guarda en evidencia; extrae texto (docling, Tesseract) y detecta datos personales (Presidio) | `egress-proxy`, Postgres, volumen de evidencia | Ninguno |
| `dispatcher` | Deployment | Envía correos aprobados y lee respuestas con la API de Gmail; entrega cada respuesta a `capture` como evidencia | Postgres, `egress-proxy` (solo gmail.googleapis.com y oauth2.googleapis.com) | Token OAuth de Gmail |
| `egress-proxy` | Deployment | Squid con lista blanca de dominios por cliente | Internet | Ninguno |
| `investigacion-pg` | Cluster CNPG | Base propia, separada del Postgres compartido, con pgvector y PostGIS | Solo desde el namespace | Credenciales CNPG |
| `evidencia` | PV estático NFS | Dataset ZFS `nvme/k8s/investigacion/evidencia` en pve | Montado en `capture` (escritura) y `web` (lectura) | No aplica |

Todos los secretos con SOPS + age (`*.sops.yml`). Ningún Ingress: acceso por NodePort desde LAN o tailnet.

## Stack Python y bibliotecas

Un solo repositorio Python (monorepo) con un paquete por servicio y un paquete común con modelos y esquemas. Se eligió Python porque las piezas reutilizables del ecosistema están en ese lenguaje; ver la [evaluación](https://claude.ai/artifact/SzMn1u9MwotcLW1PpB5TmW).

| Área | Elección | Nota |
| --- | --- | --- |
| Lenguaje y entorno | Python 3.12, `uv` para dependencias, `ruff` y `mypy` en CI | Imágenes multi-etapa, usuario no root |
| API e interfaz | FastAPI + Jinja2 + HTMX | Un usuario: interfaz renderizada en servidor, sin SPA |
| Modelos y validación | Pydantic v2; SQLAlchemy 2 + Alembic | Los esquemas de salida de agentes son modelos Pydantic |
| Flujos durables | DBOS Transact (sobre el mismo PostgreSQL) | Validar en la fase 0; alternativa: Procrastinate. Temporal queda descartado por peso |
| LLM | SDK oficial `anthropic` con tool use propio (sin frameworks de agentes en el MVP) | Modelos configurables por rol en variables de entorno |
| Modelos locales | Ollama existente (`ollama-stack`, servicio interno) | Embeddings multilingües (p. ej. `bge-m3`) y clasificación simple |
| Documentos | docling para PDF y tablas; Tesseract (español) para escaneos; `openpyxl`/`pandas` para planillas | La extracción guarda número de página y desplazamiento de caracteres |
| Datos personales | Microsoft Presidio + reconocedor propio de RUN (con dígito verificador) y modelo spaCy en español | Reemplazo por marcadores estables (`[PERSONA_3]`) para mantener coherencia |
| Captura web | `httpx` y Playwright con Chromium para portales con JavaScript | Guardar respuesta cruda y, si aplica, PDF renderizado |
| Búsqueda | pgvector (semántica) + búsqueda de texto completo de PostgreSQL en español | Sin Elasticsearch |
| Geodatos | PostGIS; mapa en la interfaz con Leaflet | Para el catastro y la ubicación de obras |
| Análisis | `pandas`/`polars`, scripts versionados en el repo | Cada cifra derivada guarda las capturas de origen |
| Pruebas | `pytest`, `testcontainers` para PostgreSQL | Pruebas de seguridad obligatorias (ver fases) |

No adoptar plataformas completas (Aleph, Datashare, Froide). Sí se puede tomar como referencia el modelo de estados de solicitudes de Froide y el esquema FollowTheMoney para entidades, si sus licencias lo permiten.

## Modelo de datos

La base de PostgreSQL es la memoria del sistema; la búsqueda semántica es un índice secundario. Todas las tablas llevan `id` UUID, `created_at` y `created_by` (`daniel`, `agent:<rol>` o `service:<nombre>`).

| Entidad | Campos clave | Reglas |
| --- | --- | --- |
| `investigation` | título, pregunta, periodo, territorio, estado | Una activa en el MVP (Villarrica) |
| `hypothesis` | investigación, código (H1…H7), enunciado, qué la sostiene, qué la descarta, estado | Precargar H1-H7 del plan |
| `organization` | nombre, tipo, dominios oficiales, URL de transparencia activa, canal preferido (correo o portal) | Los correos de contacto viven en organization\_contact (ver descubrimiento de correos) |
| `source` | organismo, URL o descripción, tipo (portal, dataset, documento, correo), periodicidad, último estado | Catálogo vivo de fuentes |
| `capture` | fuente, URL final, `sha256`, mime, tamaño, ruta de almacenamiento, estado HTTP, `fetched_at`, cabeceras | Inmutable; la ruta es `evidencia/<sha256[0:2]>/<sha256>`; misma huella = mismo archivo |
| `extraction` | captura, versión del extractor, texto por página, tablas, idioma | Varias por captura (al cambiar el extractor) |
| `pii_scan` | extracción, hallazgos por tipo, nivel (`ninguno`, `bajo`, `alto`), versión redactada | Nivel `alto` (p. ej. RUN de particulares) aísla la captura y pide revisión de Daniel |
| `chunk` | extracción, página, desplazamiento inicio/fin, texto, `embedding vector` | Solo de versiones redactadas |
| `card` (ficha) | captura, agente, esquema, JSON validado | Salida estructurada del lector |
| `citation` | ficha o hallazgo, captura, página, texto citado, desplazamientos, `verified`, versión del verificador | `verified` solo lo escribe el verificador determinista |
| `finding` (hallazgo) | investigación, hipótesis, enunciado, tipo (hecho, inferencia, hipótesis), estado (`borrador`, `pendiente`, `verificado`, `rechazado`), limitaciones | `verificado` exige todas sus citas verificadas |
| `investment_item` | código BIP o ID de licitación, organismo, financiamiento, monto aprobado, monto ejecutado, contratista (RUT de empresa), fechas, sector, `geom` | Cada fila enlaza a las capturas de origen |
| `road_segment` (catastro) | nombre, sector, urbano/rural, responsable (municipal, SERVIU, Vialidad, sin responsable), carpeta, `geom` |  |
| `road_observation` | tramo, fecha, estado (escala de 1 a 5), fotos (capturas), nota, `geom` del punto | Fotos con EXIF de fecha y ubicación; quitar EXIF del teléfono al exportar |
| `request` (solicitud) | organismo, asunto, cuerpo, adjuntos, `content_hash`, estado, `sent_at`, `due_at`, `extension_due_at`, `appeal_due_at` | Máquina de estados en la sección de flujos |
| `mail_message` | solicitud, dirección (salida/entrada), Message-ID, captura del correo original | Los correos entrantes son capturas no confiables |
| `approval` | objeto aprobado (tipo e id), `content_hash`, decisión, `decided_at`, `used_at`, comentario | Uso único; solo la crea `web` tras autenticación con TOTP |
| `task` | rol del agente, objetivo, entradas, salida (JSON), estado, costo | Una fila por paso de agente |
| `llm_call` | tarea, proveedor, modelo, tokens de entrada y salida, costo, hash del prompt, conteo de redacciones, latencia | Prompt y respuesta completos guardados como capturas internas |
| `holiday` | fecha, nombre | Feriados de Chile para el cálculo de días hábiles; carga anual revisada por Daniel |
| `audit_log` | secuencia, actor, acción, objeto, datos, `prev_hash`, `hash` | Solo inserción (permisos de BD sin UPDATE ni DELETE); exportación diaria fuera del clúster |

## Agentes, herramientas y formato de salida

Cuatro agentes, cada uno es un paso de un flujo DBOS y no una entidad que converse libremente con otros. El orquestador es código: el plan de tareas lo propone el investigador y lo aprueba Daniel. Las herramientas son funciones Python registradas por rol; una llamada a una herramienta fuera de la lista del rol se rechaza y se registra en la auditoría.

| Agente | Recibe | Herramientas permitidas | Entrega | Modelo por defecto |
| --- | --- | --- | --- | --- |
| `lector` | Texto redactado de una extracción, con página y desplazamientos | Ninguna | `card`: hechos, cifras, organismos, fechas, montos, cada uno con cita literal y ubicación | Modelo económico (`claude-haiku-5-5`) |
| `investigador` | Pregunta, hipótesis, catálogo de fuentes, fichas existentes | `search_sources` (catálogo interno), `search_web` (SearXNG propio, devuelve solo título y URL), `request_capture(url)`, `query_readonly` (vistas SQL de solo lectura) | Plan de tareas, fuentes nuevas propuestas, hallazgos en borrador con citas | Modelo intermedio (`claude-sonnet-5-5`) |
| `redactor` | Hallazgos verificados, fichas, plantillas | `get_findings`, `get_cards`, `create_draft` | Borradores de solicitudes, minutas y explicadores, cada afirmación enlazada a su hallazgo | Modelo intermedio |
| `verificador` | Hallazgos y borradores | `verify_citation` (determinista), `get_capture_text` | Lista de citas verificadas o fallidas, contradicciones, afirmaciones sin respaldo | Código primero; luego el modelo más capaz (`claude-opus-5-5`), distinto del que produjo el texto |

Los IDs de modelo van en variables de entorno por rol (`MODEL_LECTOR`, etc.) para poder cambiarlos sin desplegar código.

### Verificación de citas (determinista)

1. Normalizar Unicode (NFKC), espacios y guiones de corte de línea en el texto citado y en el texto de la extracción.
2. Buscar coincidencia exacta en la página indicada; si no, en todo el documento.
3. Coincidencia exacta: `verified = true` con la ubicación real. Coincidencia aproximada (similitud ≥ 0,97): `verified = false` con estado `revisar`, para que Daniel decida. Sin coincidencia: `verified = false`.
4. Para cifras, además se compara el número normalizado (separadores chilenos de miles y decimales).

El verificador LLM nunca cambia el estado `verified`; solo agrega observaciones.

### Formato común de salida

Cada tarea devuelve un JSON validado con Pydantic. Si no valida, se reintenta una vez y luego la tarea falla y queda visible en la interfaz.

```json
{
  "task_id": "uuid",
  "role": "investigador",
  "objective": "Identificar proyectos FNDR de vialidad en Villarrica 2019-2026",
  "findings": [{"statement": "...", "kind": "hecho|inferencia|hipotesis", "hypothesis": "H2",
                "citations": [{"capture_id": "uuid", "page": 3, "quote": "texto literal"}]}],
  "sources_proposed": [{"url": "https://...", "reason": "..."}],
  "evidence_status": "verificado|parcial|pendiente",
  "limitations": ["..."],
  "privacy_risks": ["..."],
  "recommended_action": "...",
  "requires_approval": true
}
```

## Flujos, aprobación humana y despachador de correo

### Puntos de aprobación obligatoria

Lista cerrada, definida en código. Cada aprobación se hace en la bandeja de la interfaz, muestra el contenido exacto (y el diff si hubo una versión anterior) y crea una fila en `approval` con el hash de ese contenido.

| Qué se aprueba | Quién lo propone | Qué pasa sin aprobación |
| --- | --- | --- |
| Plan de tareas de una investigación | investigador | Los agentes no se ejecutan |
| Correo de transparencia de un organismo | investigador + lector | El dispatcher no puede enviar a ese organismo |
| Envío de una solicitud por correo | redactor | El dispatcher no envía |
| Captura con datos personales de nivel alto | capture | Queda aislada, sin extracción ni envío a la API |
| Hallazgo que pasa a `verificado` con citas en estado `revisar` | verificador | Queda `pendiente` |
| Exportación de un producto para terceros | redactor | No se genera el archivo |
| Nuevo dominio en la lista blanca del proxy | investigador | Se propone como PR al repo; Flux solo lo aplica tras el merge de Daniel |

### Estados de una solicitud

| Estado | Entra cuando | Sale hacia |
| --- | --- | --- |
| `borrador` | El redactor crea el texto | `en_revision` |
| `en_revision` | Daniel la abre | `aprobada` o `borrador` (con comentarios) |
| `aprobada` | Daniel aprueba; se fija el `content_hash` | `enviada` o `error_envio` |
| `enviada` | El dispatcher recibe aceptación SMTP; se calcula `due_at` | `con_acuse`, `respondida`, `prorrogada`, `vencida` |
| `con_acuse` | Llega el acuse o el número de folio del organismo | `respondida`, `prorrogada`, `vencida` |
| `prorrogada` | El organismo notifica prórroga; se calcula `extension_due_at` | `respondida`, `vencida` |
| `respondida` | Llega una respuesta y Daniel la clasifica como completa, parcial o denegada | `cerrada` o `reclamable` |
| `vencida` | Pasa `due_at` (o `extension_due_at`) sin respuesta | `reclamable` |
| `reclamable` | Se calcula `appeal_due_at`; alerta a Daniel | `cerrada` (Daniel registra si presentó el reclamo) |

### Plazos

- Días hábiles: lunes a viernes, excluyendo feriados de la tabla `holiday`.
- Respuesta: 20 días hábiles desde la recepción; prórroga de hasta 10 días hábiles si el organismo la notifica.
- Reclamo ante el Consejo para la Transparencia: 15 días hábiles desde la respuesta o desde el vencimiento.
- La regla exacta de cómputo (desde el envío o desde el acuse, envíos fuera de horario) queda en un módulo aislado y con pruebas, y Daniel la confirma antes de la fase 3.
- Alertas por Telegram (el mismo canal que usa Flux) a 5 días hábiles, 1 día hábil y al vencer.

### Despachador de correo

Usa una cuenta Gmail creada solo para la investigación, sin ningún uso personal, con verificación en dos pasos.

- **API de Gmail, no SMTP/IMAP.** La API va por HTTPS y por lo tanto pasa por el `egress-proxy` con lista blanca, cosa que SMTP e IMAP no permiten. Cliente: `google-api-python-client`.
- **Permisos (scopes):** `gmail.send` para enviar y `gmail.modify` para leer y etiquetar los correos ya procesados. El token da acceso a todo el buzón, por eso solo lo tiene el `dispatcher`.
- **OAuth:** proyecto propio en Google Cloud con la aplicación en estado "En producción". En estado "Prueba" los refresh tokens vencen a los 7 días y el envío se cortaría. Para uso propio Google muestra una advertencia de app no verificada, que Daniel acepta una vez. Validar este punto al inicio de la fase 3.
- **Contenido mínimo de cada solicitud** (artículo 12 de la Ley 20.285): nombre y apellidos de Daniel, dirección o correo para notificaciones, identificación clara de la información pedida, órgano destinatario y firma. La plantilla del redactor los incluye siempre.

Antes de cada envío el dispatcher comprueba, en este orden, y si una falla no envía y registra el motivo:

1. Existe `approval` con decisión `aprobada`, sin `used_at`, para esa solicitud.
2. El SHA-256 del mensaje que va a enviar (destinatario, asunto, cuerpo y adjuntos) es igual al `content_hash` aprobado.
3. El destinatario es un `organization_contact` en estado `confirmado` de ese organismo, y su verificación contra la fuente tiene menos de 90 días (si no, se vuelve a ejecutar el descubrimiento antes de enviar).
4. No se superó el límite de envíos: máximo 2 solicitudes por organismo por semana y 10 en total por día (configurable).
5. El servicio no está en modo `dry_run` (por defecto lo está hasta aprobar la fase 3).

Tras enviar: marca `used_at`, guarda el `.eml` enviado como captura, registra el Message-ID y escribe en la auditoría.

Recepción: cada 10 minutos consulta la bandeja con la API de Gmail, guarda cada correo (formato `raw`) y cada adjunto como captura no confiable, y lo asocia a la solicitud por `In-Reply-To`, `References`, el hilo de Gmail o el folio en el asunto. Después le pone la etiqueta `procesado`. Los que no se pueden asociar quedan en una bandeja manual. Ningún agente lee la bandeja directamente.

### Descubrimiento de correos de transparencia

Los organismos publican su correo o canal para solicitudes de acceso en su transparencia activa o en su sitio. La plataforma debe encontrarlo sola, porque enviar a una dirección equivocada no tiene vuelta atrás. Flujo `discover_contact(organismo)`:

1. **Buscar:** el investigador localiza páginas candidatas (sección de transparencia, "Solicitud de acceso a la información", ficha del organismo en el Portal de Transparencia). Solo dominios oficiales del organismo y `portaltransparencia.cl`.
2. **Capturar:** `capture` guarda cada página con hash y fecha.
3. **Extraer:** el lector devuelve cada correo encontrado con la frase que lo rodea, como cita literal, y lo clasifica: `transparencia` (dice expresamente que es para solicitudes de la Ley 20.285), `oirs` o `contacto_general`.
4. **Validar (código):** formato válido; la dirección aparece literalmente en la captura (verificador de citas); la página está en un dominio oficial del organismo; el dominio del correo es del organismo o estatal (`gob.cl`, `gov.cl` o el dominio municipal); el dominio tiene registro MX. Un correo en dominio genérico (gmail.com, hotmail.com) queda en `revisar`.
5. **Elegir:** se prefiere `transparencia` sobre `oirs` y esta sobre `contacto_general`. Si hay varios del mismo tipo, se muestran todos.
6. **Confirmar:** Daniel ve el correo, la cita y la captura, y lo confirma (punto de aprobación). Si más tarde la fuente cambia la dirección, el contacto anterior pasa a `obsoleto` y hace falta una nueva confirmación.
7. **Sin correo publicado:** si el organismo solo acepta el formulario del Portal de Transparencia, el canal queda como `portal`. La plataforma prepara el texto y Daniel lo ingresa a mano y registra el folio. El MVP no automatiza formularios web.

Tabla `organization_contact`: organismo, correo, tipo, captura fuente, cita, estado (`candidato`, `revisar`, `confirmado`, `obsoleto`), `confirmed_at`, `last_verified_at`.

## Fuentes iniciales para Villarrica

Datos semilla que la migración inicial debe cargar. El detalle y la justificación de cada fuente están en el [plan de investigación](https://claude.ai/artifact/6MpKbUnD6pQpaox4yVnpAK).

- **Investigación:** presupuesto y ejecución en caminos urbanos y rurales de Villarrica, 2019-2026.
- **Hipótesis:** H1 a H7 del plan (presupuesto, ejecución, calidad de obras, caminos sin responsable, priorización territorial, factores físicos, irregularidades).
- **Organismos:** Municipalidad de Villarrica, SERVIU Araucanía, Gobierno Regional de La Araucanía, Dirección de Vialidad Araucanía (MOP). Los correos de transparencia los descubre la plataforma desde las páginas oficiales y Daniel los confirma (ver descubrimiento de correos).

### Lista blanca inicial del proxy

Cada dominio se confirma en la fase 0 con una captura real; los no confirmados quedan fuera hasta que funcionen.

| Dominio | Uso | Cliente | Estado |
| --- | --- | --- | --- |
| `bid-ckan.ministeriodesarrollosocial.gob.cl` | Banco Integrado de Proyectos (CSV 2019-2027) | capture | Confirmado el 27-oct-2026 |
| `api.mercadopublico.cl`, `www.mercadopublico.cl`, `datos-abiertos.chilecompra.cl` | Licitaciones y órdenes de compra (la API requiere ticket gratuito) | capture | Por confirmar; el portal de datos abiertos necesita Chromium |
| `www.sinim.gov.cl` | Presupuesto municipal comparable | capture | Por confirmar; rechazó la descarga automatizada |
| `www.portaltransparencia.cl` | Transparencia activa de los cuatro organismos | capture | Por confirmar |
| Sitios de la Municipalidad, SERVIU, GORE y MOP/Vialidad | Transparencia activa propia, actas, decretos | capture | Dominios exactos por confirmar |
| `www.dipres.gob.cl` | Ejecución presupuestaria MINVU y MOP | capture | Por confirmar |
| `www.contraloria.cl` | Informes de fiscalización | capture | Por confirmar |
| `www.infolobby.cl`, `www.infoprobidad.cl` | Audiencias y declaraciones de intereses | capture | Por confirmar |
| `www.bcn.cl` | Texto de la Ley 20.285 y normas citadas | capture | Por confirmar |
| Motores que use SearXNG | Búsqueda web del investigador | searxng | Definir en la fase 2 |
| `api.anthropic.com` | API de Claude | llm-gateway | Requerido |
| gmail.googleapis.com, oauth2.googleapis.com | Correo | dispatcher | Requerido (cuenta Gmail dedicada) |

Nota: la búsqueda web del investigador requiere agregar un `searxng` al namespace, con salida solo por el proxy. Devuelve títulos y URLs; nunca fragmentos de texto, que serían contenido no confiable fuera del lector.

## Seguridad, observabilidad y respaldos

### Seguridad

- **Acceso:** usuario único con contraseña (argon2) y TOTP; sesión de 12 horas; cada aprobación pide de nuevo el TOTP. Servicio expuesto solo por NodePort, accesible desde LAN o tailnet.
- **NetworkPolicies:** denegar todo por defecto en el namespace (entrada y salida) y abrir solo las conexiones del diagrama, más DNS. Verificar que el controlador de políticas de red de k3s está activo antes de confiar en ellas.
- **Pods:** `runAsNonRoot`, `readOnlyRootFilesystem`, sin capacidades, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`. Un ServiceAccount por workload.
- **Secretos:** SOPS + age; el token OAuth de Gmail solo se monta en `dispatcher` y la clave de la API solo en `llm-gateway`.
- **Base de datos:** un rol de PostgreSQL por servicio con los permisos mínimos; el rol del worker no puede escribir `approval` ni `citation.verified`; nadie tiene UPDATE ni DELETE sobre `audit_log` y `capture`.
- **Prompt injection:** delimitadores y etiquetas de procedencia en todo contenido externo; el lector no tiene herramientas; los esquemas de salida se validan; ninguna salida de un agente se ejecuta como código ni como SQL libre.
- **Captura:** límite de tamaño por archivo (100 MB, configurable), tiempo máximo por descarga, Chromium sin perfil persistente y sin descargas automáticas de ejecutables.
- **Costos:** límite diario y mensual de gasto en la API en `llm-gateway`; al superarlo, rechaza llamadas y alerta.

### Observabilidad

- Cada servicio expone `/metrics` (Prometheus) para el VictoriaMetrics existente: llamadas al LLM, tokens, costo, errores, capturas, verificaciones fallidas, envíos bloqueados.
- Panel de Grafana "Investigación" y reglas en vmalert: gasto diario sobre el límite, envío bloqueado por hash distinto, captura con datos personales de nivel alto, plazos por vencer, cadena de auditoría rota.
- Logs estructurados en JSON con `task_id` y `investigation_id`.

### Respaldos

- Base: respaldo de CNPG propio (dump diario a `nvme/k8s/backups/postgres/investigacion`, retención 14 días) además del `pg_dumpall` existente si cubre este cluster.
- Evidencia: dataset ZFS propio con snapshots de sanoid y copia fuera de casa con restic a Cloudflare R2.
- Auditoría: exportación diaria del `audit_log` con el hash final del día, copiada a R2.
- Prueba de restauración completa (base + evidencia + verificación de hashes) antes de cerrar la fase 1 y luego cada trimestre.

## Fases, entregables y criterios de aceptación

Cada fase se entrega como uno o más PR a `main` del repo `homelab-config` (despliegue) y del repo de la aplicación, siguiendo las reglas del [entorno del homelab](https://claude.ai/artifact/QWy3zKLUVc4tADWE2J6a2X). Una fase se cierra solo cuando Daniel revisa sus criterios.

1. **Fase 0: base de infraestructura.**
   - Entregables: namespace `investigacion` con NetworkPolicies de denegación por defecto; Cluster CNPG `investigacion-pg` con pgvector y PostGIS; dataset ZFS y PV de evidencia; `egress-proxy` con lista blanca; secretos SOPS; CI que construye y publica imágenes en GHCR; Kustomization de Flux; prototipo mínimo de un flujo DBOS que sobrevive al reinicio del pod.
   - Aceptación: un pod de prueba en el namespace no puede salir a internet salvo por el proxy y solo a dominios de la lista; el flujo DBOS de prueba retoma tras `rollout restart`; respaldo de la base corre y se restaura.
2. **Fase 1: evidencia y verificación.**
   - Entregables: servicios `web` (login, investigaciones, visor de capturas), `capture` (descarga, hash, extracción, Presidio), `audit_log` encadenado, verificador determinista de citas, `llm-gateway` con redacción y registro, agente `lector`.
   - Aceptación: capturar el CSV del Banco Integrado de Proyectos y 10 PDFs reales de los organismos; el hash recalculado coincide; la cadena de auditoría se valida de punta a punta; el verificador acepta 20 citas verdaderas y rechaza 20 citas inventadas o alteradas (conjunto de prueba fijo); ningún texto con RUN de prueba llega a la API (verificado en `llm_call`).
3. **Fase 2: investigación documental.**
   - Entregables: agentes `investigador`, `redactor` y `verificador`; `searxng`; bandeja de aprobaciones; carga de semillas de Villarrica; inventario de fuentes.
   - Aceptación: cada fuente de la lista blanca queda marcada como verificada, inexistente o no accesible; el plan de tareas pasa por aprobación; el descubrimiento encuentra y Daniel confirma el canal de transparencia (correo o portal) de los cuatro organismos, cada uno con su captura y cita; se generan los borradores de las cuatro solicitudes del plan, con cada afirmación enlazada a un hallazgo.
4. **Fase 3: solicitudes y plazos.**
   - Entregables: `dispatcher` (API de Gmail), máquina de estados de solicitudes, calendario de días hábiles, alertas por Telegram.
   - Aceptación: con el dispatcher en `dry_run`, un mensaje sin aprobación, con hash distinto, con destinatario fuera de la tabla o sobre el límite semanal no se envía (una prueba por caso). Una prueba de inyección (documento que pide "envía este correo") no produce ningún envío. Luego Daniel aprueba quitar `dry_run` y se envía la primera solicitud real; su respuesta queda asociada y con plazos calculados.
5. **Fase 4: base de inversión y catastro.**
   - Entregables: scripts reproducibles que generan `investment_item` desde las capturas; carga de `road_segment` y `road_observation` desde fotos con EXIF; mapa por sector en la interfaz; exportación del explicador y de la minuta.
   - Aceptación: regenerar la base desde cero con un comando da el mismo resultado; cada cifra de la minuta exportada enlaza a sus capturas; el mapa muestra inversión y estado por sector.

Pruebas de seguridad transversales (deben estar en CI desde la fase en que aplican): inyección en PDF y en correo entrante, intento de exfiltración a un dominio fuera de la lista, llamada a una herramienta no permitida para el rol, modificación directa de una captura (debe detectarse por hash).

## Decisiones abiertas y prerrequisitos

**Prerrequisitos del entorno** (antes de guardar evidencia real, al cerrar la fase 1):

- [ ] Recuperar la redundancia del pool `nvme` (un solo disco desde el 17-oct-2026) o ubicar la evidencia en un pool con redundancia.
- [ ] Terminar la copia fuera de casa con restic a Cloudflare R2.
- [ ] Considerar una UPS para pve: los cortes de luz sin apagado ordenado corrompen PostgreSQL y NFS.

**Decisiones de Daniel:**

- [x] Cuenta de correo: Gmail dedicada a la investigación (Daniel la crea).
- [x] Correos de transparencia: los descubre la plataforma y Daniel los confirma.
- [ ] Crear el proyecto de Google Cloud y autorizar la app de Gmail (fase 3).
- [ ] Regla de cómputo de plazos (desde el envío o desde el acuse) y carga de feriados 2026-2027.
- [ ] Límite mensual de gasto en la API de Claude.
- [ ] Puerto NodePort para `web`.

**Decisiones técnicas del equipo de desarrollo** (documentar en el repo como ADR):

- [ ] DBOS frente a Procrastinate, tras el prototipo de la fase 0.
- [ ] Imagen de CNPG con pgvector y PostGIS juntos, o extensión compilada.
- [ ] Si `capture` necesita aislamiento adicional (gVisor u otro runtime) para Chromium.
