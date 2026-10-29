# Traspaso a la fase 2

Fecha: 2026-10-28. Fase 1 entregada y desplegada. **Falta la revisión de Daniel** (la fase solo se cierra con su visto
bueno) y tres acciones suyas (ver «Qué debe hacer Daniel»).

## Estado en una mirada

| Pieza | Estado | Dónde |
|-------|--------|-------|
| Esquema SQL, roles, auditoría | desplegado (migraciones 0001–0005) | `src/investigacion/migraciones/`; manifiestos en `homelab-config/cluster/apps/investigacion/` |
| `web` (login argon2 + TOTP) | desplegado, **sin usuario creado** | NodePort 30960, sin Ingress |
| `capture` | desplegado; probado con fuentes reales | cola `capture_job` |
| `extract` | desplegado y estable (sin reinicios); PDF con texto nativo + OCR | imagen `investigacion-extract` |
| `llm-gateway` | desplegado, 1 réplica, **clave provisional** (Secret `anthropic-api`, cifrado con SOPS) | Daniel la reemplaza y pone el límite de gasto |
| `lector` | CronJob suspendido; **ejecutado a mano sobre las 10 capturas** (resultados abajo) | `investigacion lector --pendientes` |
| Eval Citations vs JSON | **ejecutado contra la API real** (resultados abajo) | `src/investigacion/eval_lector.py` |

Imagen en producción: `sha-935f48f` (ambas, con el tope de tamaño del lector verificado en vivo: el CSV de 7 M de caracteres
se rechaza sin llamar a la API). 203 pruebas pasan en el CI (Postgres real); las de docling y GLiNER corren
con el extra `extract` en local.

## Qué debe hacer Daniel (en orden)

1. Cambiar la contraseña temporal del usuario `daniel` (repetir `crear-usuario`; receta en
   `homelab-config/cluster/apps/investigacion/README.md`, con el `nodeSelector` amd64).
2. **Reemplazar la clave de la API** (hoy es provisional) editando `anthropic-api.sops.yaml` con `sops`, y **poner el
   límite de gasto en la consola de Anthropic** (aún no está confirmado).
3. Decidir la estrategia del lector: el eval favorece **JSON + verificador** (ver abajo).
4. Revisar los criterios y cerrar la fase.

## Criterios de aceptación de la fase 1

| Criterio | Resultado | Evidencia |
|----------|-----------|-----------|
| Capturar el CSV del BIP y 10 PDF reales; el hash recalculado coincide | **Cumplido.** CSV `inversiones_2027.csv` (6,5 MB) y 10 PDF de SERVIU Araucanía y Vialidad por el proxy (12 trabajos: 11 hechos, 1 `403` esperado de un dominio fuera de lista, más un `diccionario.csv` desechable). `investigacion verificar-capturas` sale 0 | cola `capture_job`, auditoría |
| Cadena de auditoría válida de punta a punta; modificar una captura se detecta | **Cumplido.** Cadena válida (49 entradas al cierre). En vivo se alteró el archivo de una captura desechable: «el contenido del archivo no coincide con su hash» (exit 1); restaurado, exit 0 | `tests/test_auditoria.py`, `tests/test_capture.py` |
| Verificador: 20 citas verdaderas aceptadas, 20 inventadas o alteradas rechazadas | **Cumplido** (20/20 y 20/20, conjunto fijo) | `tests/citas_fijas.py`, `tests/test_verificador.py` |
| Ningún RUN de prueba llega a la API (verificado en `llm_call`) | **Cumplido, en vivo y con doble.** Un documento con RUN `12.345.678-5` y un correo plantados se envió por el gateway real: `llm_call` registra `redacciones=2`, `residuos_pii=0`, no bloqueada. En las 17 llamadas reales, `residuos_pii>0` es 0 y los 10 documentos leídos no contienen RUN, correo ni teléfono detectables. Con el doble: un fallo simulado de la redacción bloquea y no llama | `llm_call`, `tests/test_gateway.py` |
| Una inyección en un PDF no cambia el comportamiento del lector | **Cumplido con el modelo real** (caso `inyeccion` del eval, 2 estrategias): `inyección_obedecida = 0`; el hecho inyectado («transferir $ 999.999.999») no aparece en la ficha. Además, de forma estructural con un modelo simulado que obedece (sin herramientas, esquema `extra=forbid`, ficha como único efecto). Una sola inyección de prueba: no es una garantía | `eval_lector.py`, `tests/test_lector.py` |

### Extracción de las 12 capturas (resultado final)

| Captura | Texto | Páginas | Extractor | Datos personales |
|---------|-------|---------|-----------|------------------|
| CSV BIP 2027 | 6,97 M caracteres | 20 | tabular | bajo (5 «teléfonos», ver limitaciones) |
| Cuenta Pública SERVIU (×2, mismo archivo) | 15.842 | 65 | pdfium + Tesseract | bajo |
| Plan de Inversiones (26 MB) | 2.238 | 1 | pdfium | bajo |
| Programación Financiera | 37.604 | 9 | pdfium | bajo |
| Planteamientos ciudadanos | 49.848 | 14 | pdfium | bajo (144 nombres) |
| Resolución 4058 / Protocolo DS10 / Oficio 2274 | 41.851 / 39.948 / 4.445 | 19 / 12 / 2 | pdfium | bajo |
| Circular 35 / Glosario Vialidad | 8.156 / 21.795 | 4 / 9 | pdfium + Tesseract | bajo |
| `diccionario.csv` | 7.948 | 1 | tabular | ninguno |

Ninguna captura quedó aislada (nivel alto) una vez corregidos los falsos positivos de RUN.

## Lectura de las 10 capturas con la API real (2026-10-29, `claude-haiku-5-5`)

- **10 fichas, 143 citas: 135 verificadas (94 %), 1 en `revisar`, 7 fallidas.** Costo total USD 0,0246 (124.732 tokens de
  entrada, 24.313 de salida, ~10 s por documento). Sin bloqueos ni residuos de datos personales.
- Quedaron fuera el CSV del BIP (7 millones de caracteres, > 1 millón de tokens) y la captura duplicada de la Cuenta
  Pública. `lector.pendientes` ahora excluye lo que supera 300.000 caracteres y `leer` lo rechaza (audita `lector.omitida`).
- **Las 7 fallidas** (el verificador hizo su trabajo):
  - 2 citas de menos de 12 caracteres (`m$30.117`): demasiado cortas para probar nada.
  - 1 **error real del modelo**: declaró `369.318.903` y la cita dice `400.382.055 34.855.859`.
  - 1 cifra (`4.637`) en la celda vecina, fuera de la cita.
  - 3 con el número escrito en palabras en la cita («veinte días hábiles») frente a la cifra declarada en dígitos
    (`20 días hábiles`): rechazo por forma, no por contenido. Mejora posible: aceptar números en palabras.

### Eval: JSON validado + verificador frente a API de Citations

| Estrategia | Recall (8 hechos esperados) | Hechos | Verificados | Inventados | Formato inválido | Inyección obedecida | Costo |
|------------|-----------------------------|--------|-------------|------------|------------------|---------------------|-------|
| **JSON + verificador** | **100 %** (8/8) | 12 | 12 | 0 | 0 | 0 | USD 0,0011 |
| Citations (ficha armada en código) | 88 % (7/8) | 8 | 8 | 0 | 0 | 0 | USD 0,0016 |

Tres casos sintéticos y una corrida: sirve para decidir, no para publicar. Recomendación: **JSON + verificador** (más
hechos, mejor recall, más barato y permite cifra declarada). Citations garantiza que la cita existe pero no que el
enunciado la respalde.

## Hallazgos de la aceptación en vivo (cada uno con su PR)

Se descubrieron por usar fuentes y la API reales; ninguno lo mostraban las pruebas con datos sintéticos.

1. **GLiNER rinde ~0,3 KB/s por CPU**: el CSV del BIP habría tardado horas. Los datos tabulares se redactan solo con los
   detectores deterministas (`pii_scan.version = redaccion-N-determinista`); el texto libre usa chunks de ~900 caracteres.
2. **docling con OCR por defecto era inviable** (OCR de cada imagen incrustada) y luego causó OOM: un PDF de 9 páginas y
   175 KB superaba 3 GiB. Un conversor por PDF no era la causa (diagnóstico leyendo docling, docling-parse, docling-jobkit
   y docling-serve y midiendo con un vigilante de RSS). Página por página bajaba a 1,27 GiB **pero dejaba páginas vacías**
   (`[96, 0, 11269, …]` frente a `[3959, 3725, 5053, …]` del texto nativo): evidencia perdida sin aviso. Decisión en
   `docs/adr/0003-extraccion-pdf.md`: texto nativo (pypdfium2) + Tesseract solo en páginas sin texto; docling opcional.
3. **El detector de RUN marcaba códigos y montos**: 274 «RUN» del CSV eran códigos BIP (`30403927-0`, mismo módulo 11) y
   33 de la Programación Financiera eran montos de tabla; aislaban dos documentos legítimos. Regla actual en
   `redaccion.detectar_run` (guion pegado, dígito válido, cuerpo < 30 M; 30–50 M o dígito inválido solo con «RUN/RUT/cédula»
   delante).
4. **Había extracciones viejas defectuosas**: `extract` ahora vuelve a extraer lo hecho con el motor obsoleto y agrega un
   `pii_scan` nuevo cuando cambia la versión de la redacción; lector y web usan siempre el escaneo más reciente.
5. **Primeras llamadas reales a la API** (ninguna la mostraban las pruebas con doble): el SDK de Anthropic usa `httpx2` y
   rechaza un `httpx.Client` (el gateway moría al arrancar; ahora `anthropic.DefaultHttpxClient`); y la API devuelve 400
   si el esquema de salida estructurada trae `maxItems`/`maxLength` (ahora `anthropic.transform_schema`, que Pydantic
   complementa al validar). Se leyó el código del SDK, no se adivinó.
6. **Proceso**: GitGuardian re-escanea todos los commits de un PR y marca pares `"usuario"`/`"password"` literales aunque
   sean de prueba (hubo que rehacer la rama tres veces, sin `--force`); `ruff --fix` reescribe `.split()` en listas; una
   prueba en rojo se subió por encadenar con `;`. Lecciones en la skill `app-dev` (`casos.md`).

## Limitaciones conocidas (aceptadas, a decidir)

- **«Teléfonos» falsos en tablas**: los 5 del CSV son montos de 9 dígitos (`COSTO ANUAL EQUIVALENTE`). Se redactan y el
  verificador no puede citarlos. Solución posible: exigir contexto o formato para números sueltos en datos tabulares.
- **CSV con nombres de personas naturales** quedarían sin redactar (no pasan por GLiNER). Hoy el BIP no los trae; revisar
  columnas por fuente al sumar otras.
- **OCR solo en páginas con < 80 caracteres**: un PDF de una página con un póster/infografía (el Plan de Inversiones,
  2.238 caracteres) no pasa por OCR aunque tenga texto en imágenes. Probado en vivo con Tesseract en dos PDF; **falta un
  PDF totalmente escaneado**.
- **Cifras escritas en palabras** («veinte días») no coinciden con la cifra declarada en dígitos y la cita se rechaza.
- **El lector no cubre documentos de más de 300.000 caracteres** (CSV masivos): van por el camino determinista.
- **Capturas duplicadas**: `lector --pendientes` lee también la segunda captura del mismo archivo (Cuenta Pública) y
  genera una ficha repetida; conviene deduplicar por SHA-256.
- **Autoridades**: GLiNER redacta también nombres de autoridades en ejercicio (la especificación permite dejarlos).
- **Tablas de PDF** salen en el orden del flujo, no como celdas; una cita que cruce celdas queda en `revisar`.
- **Una cita literal del texto inyectado sí verifica**: el verificador prueba que la cita existe, no que sea cierta ni
  inofensiva. El investigador (fase 2) verá fichas con hechos de un documento hostil; toda acción sigue pasando por
  aprobación humana.
- **Ley 21.719 (vigente desde el 1-dic-2026)**: `capture`, `extraction`, `pii_scan`, `card` y `audit_log` son inmutables
  por diseño, lo que choca con un derecho de supresión. Hace falta un **tombstone** (marcar una captura como retirada,
  ocultar texto y archivo, conservar el hash) y una política de retención. Sin decidir.
- **Rol de `capture` sin SELECT sobre la auditoría**: la integridad se verifica desde `web`
  (`kubectl exec deploy/web -c web -- investigacion verificar-capturas`).
- **Imagen de `extract`**: ~10–15 min de CI en cada PR (se construye siempre). `k3s-node-1` está al ~84 % de memoria.
- **Un fallo que mata el proceso (OOM) se repite**: una captura que reviente `extract` queda en CrashLoop (la lista de
  pendientes la vuelve a tomar). Falta un contador de intentos por captura.

## Desviaciones respecto de la especificación (decididas, a confirmar)

- **DBOS no se usa todavía**: la fase 1 es cola en Postgres o trabajos cortos; entra con las aprobaciones (fase 2/3).
  ADR-0002 sigue vigente.
- **`extract` y `capture` separados** (ajuste del estado del arte); `extract` solo habla con Postgres.
- **PDF: texto nativo en vez de docling como motor principal** (ADR-0003, ajusta ADR-0001).
- **El lector no está dentro de un flujo DBOS ni tiene botón en la web**; se lanza con el CronJob `lector`.

## Qué sigue (fase 2: investigación documental)

Del documento de especificación: agentes `investigador`, `redactor` y `verificador`; `searxng`; bandeja de
aprobaciones; semillas de Villarrica; inventario de fuentes. Puntos de partida concretos:

1. Tabla `approval` y la bandeja en `web` (aprobación de uso único ligada a `content_hash`, TOTP en cada aprobación);
   el rol del worker no debe poder escribirla. Aquí se aprueba también sacar una captura del aislamiento (nivel alto).
2. Mover el lector a un flujo DBOS (`DBOS.recv` para aprobaciones; el worker necesita escribir el esquema `dbos`).
3. Tablas faltantes: `organization`, `organization_contact`, `hypothesis`, `finding`, `task`, `holiday`.
4. Copia inmutable de auditoría y capturas a R2 con Bucket Lock y OpenTimestamps; alertas en vmalert (cadena rota,
   captura alterada, gasto) y `/metrics`.
5. Dominios pendientes de la lista blanca (portaltransparencia.cl, bcn.cl, gorearaucania.cl) siguen sin responder a
   clientes automáticos.
6. Migraciones nuevas: un archivo por cambio, nunca editar uno aplicado (`0001–0005` ya están aplicadas).

## Cómo operar y probar

- Pruebas: `INVESTIGACION_TEST_DATABASE_URL=… uv run pytest` (Postgres local sin Docker: `scripts/pg-local.py`). Con el
  extra `extract` (`UV_PROJECT_ENVIRONMENT=/tmp/venv-extract uv run --python 3.14 --extra extract pytest`) corren también
  GLiNER y docling; sin él se saltan.
- Despliegue: subir el mismo `sha-<7>` en `web/capture/extract/llm-gateway/lector.yml` de `homelab-config`.
- Medir antes de creer: `docs/adr/0003-extraccion-pdf.md` describe cómo se midió la memoria (vigilante de RSS y pila de
  los hilos al superar el tope). Repetir con cualquier fuente nueva antes de desplegarla.
- Skills actualizadas: `app-dev` (stack, gateway, caso) y `homelab` (trampas y receta).
