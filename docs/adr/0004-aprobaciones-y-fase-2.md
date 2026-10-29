# ADR-0004: Aprobaciones en la base y decisiones de la fase 2

Estado: Aceptado · Fecha: 2026-10-29

## Contexto

La fase 2 agrega agentes que proponen (plan, hallazgos, contactos, borradores) y una bandeja donde Daniel aprueba. La
regla «nada avanza sin aprobación» no puede depender solo del código del worker: un error o una inyección que influya
en sus argumentos no debe poder saltársela.

## Decisión

- **La base impone las aprobaciones** (migración 0006). `approval` solo la inserta `web` (sin `used_at`), es
  inmutable salvo marcar `used_at` una vez, y solo se consume con `approval_usar()` (SECURITY DEFINER), que compara
  tipo, objeto, hash y vencimiento y audita el resultado. El worker no tiene INSERT ni UPDATE sobre ella.
- **El hash de lo aprobado lo calcula la base** (columnas generadas con `hash_texto()`): quien propone no declara su
  propio hash. Planes, contactos, hallazgos y borradores no cambian de contenido; una corrección es una fila nueva
  (la bandeja muestra el diff).
- Triggers que fallan cerrados: una tarea no pasa a `en_curso` sin la aprobación usada de su plan; un contacto no pasa
  a `confirmado` sin la de su contenido; un hallazgo no es `verificado` sin todas sus citas verificadas (o la
  aprobación si hay citas en `revisar`); una afirmación de un borrador exige un hallazgo verificado; una captura
  aislada solo se libera con aprobación sobre el hash de su texto redactado.
- **Un código TOTP por aprobación** (`totp_paso` UNIQUE, además de la regla de no reutilizar de `web`).
- **DBOS del worker en su propio esquema** `dbos_worker` (USAGE y CREATE para `inv_worker`). `web` no toca DBOS: el
  worker detecta las aprobaciones nuevas y despierta al flujo con `DBOS.send`; el flujo vuelve a comprobar con
  `approval_usar()`, así que un mensaje falso no aprueba nada.
- Las pruebas migran con un dueño **sin superusuario ni CREATEROLE**, como en el cluster: `CREATE SCHEMA …
  AUTHORIZATION inv_worker` pasaba en local y fallaba en la base real.

## Decisiones de Daniel (2026-10-29)

- **Ley 21.719 (retiro de capturas)**: se pospone a la fase 3. Mientras tanto las capturas con datos personales de nivel
  alto quedan aisladas y solo se liberan con aprobación.
- **Motor de PDF**: confirmado texto nativo + Tesseract (ADR-0003).
- **Clave de la API**: reemplazada por Daniel y con límite de gasto en la consola de Anthropic.
- **Lector**: JSON validado + verificador (no Citations). El redactor y el verificador usan el mismo patrón.

## Consecuencias

- Feriados: la tabla existe, pero la carga anual la revisa Daniel en la fase 3.
- El esquema `dbos` del prototipo de la fase 0 queda sin uso; se puede borrar a mano.
