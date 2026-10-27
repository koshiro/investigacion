# Plazos de la Ley 20.285 (regla de cómputo de la plataforma)

Fuente: Consejo para la Transparencia, «Compendio Jurídico 2026»
(https://www.consejotransparencia.cl/wp-content/uploads/2026/05/Compendio-Juridico-2026.pdf), que reproduce la Ley
20.285 y su reglamento (DS 13 de 2009). Leído el 2026-10-27. Las citas son literales; la página es la del compendio.

## Texto legal

**Días hábiles** (Ley de Transparencia, definiciones, pág. 44): «Días hábiles o plazo de días hábiles: es el plazo de
días establecido en el artículo 25 de la ley N° 19.880, sobre Bases de los Procedimientos Administrativos que rigen los
Actos de la Administración del Estado, entendiéndose por inhábiles los sábados, los domingos y los festivos.»

**Respuesta** (art. 14, pág. 51): «deberá pronunciarse sobre la solicitud, sea entregando la información solicitada o
negándose a ello, en un plazo máximo de veinte días hábiles, contado desde la recepción de la solicitud que cumpla con
los requisitos del artículo 12. Este plazo podrá ser prorrogado excepcionalmente por otros diez días hábiles, cuando
existan circunstancias que hagan difícil reunir la información solicitada, caso en que el órgano requerido deberá
comunicar al solicitante, antes del vencimiento del plazo, la prórroga y sus fundamentos.»

**Reclamo de amparo ante el Consejo** (art. 24, pág. 55; igual en el art. 44 del reglamento, pág. 98): «La
reclamación deberá presentarse dentro del plazo de quince días, contado desde la notificación de la denegación de
acceso a la información o desde que haya expirado el plazo previsto en el artículo 14 para la entrega de
información.»

**Notificación por correo** (reglamento, art. 27, pág. 93-94): si el solicitante pide ser notificado por correo
electrónico, «los plazos comenzarán a correr a partir del día siguiente hábil del envío del correo electrónico.»

## Regla que implementa la plataforma (módulo aislado, con tests)

| Plazo | Inicio | Duración | Notas |
|-------|--------|----------|-------|
| `due_at` (respuesta) | día hábil siguiente a la recepción de la solicitud (acuse o fecha de envío aceptado) | 20 días hábiles | Toda solicitud pide notificación por correo (art. 27 del reglamento) |
| `extension_due_at` | desde `due_at` original | +10 días hábiles | Solo si el organismo comunica la prórroga **antes** de `due_at`; si llega después, no extiende y se registra |
| `appeal_due_at` (amparo) | día hábil siguiente a la notificación de la denegación, o al vencimiento de `due_at`/`extension_due_at` | 15 días | Ver abajo |

Días hábiles: lunes a viernes, excluidos los festivos de la tabla `holiday`.

**Amparo: «quince días» sin «hábiles».** La ley y el reglamento no dicen «hábiles» en este plazo, a diferencia del
art. 14. Mientras no se confirme el criterio del Consejo, la plataforma calcula las dos fechas (15 días corridos y 15
días hábiles), **alerta por la más temprana** y muestra ambas. Así un error de interpretación nunca hace perder el
plazo.

## Cambio legal en curso

El compendio marca textos de la Ley 20.285 con vigencia diferida al **1 de diciembre de 2026** (Ley N° 21.719, de
2024, sobre protección de datos personales). Revisar la versión vigente desde esa fecha antes de la fase 3. La Ley
21.719 también aplica a cómo la plataforma trata datos personales de terceros: tenerla en cuenta en la fase 1
(redacción, aislamiento de capturas con datos de nivel alto, retención).
