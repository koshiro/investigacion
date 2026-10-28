# ADR-0003: Extracción de PDF con texto nativo; docling solo opcional

Estado: Aceptado · Fecha: 2026-10-28 · Ajusta ADR-0001 (fila «Documentos»)

## Contexto

ADR-0001 eligió docling + Tesseract. En la aceptación de la fase 1, `extract` quedó en CrashLoop (OOMKilled, 4 GiB) con
un PDF real de 9 páginas y 175 KB (la «Programación Financiera» de SERVIU Araucanía). Se midió, con un vigilante de
RSS y leyendo el código de docling, docling-parse, docling-jobkit y docling-serve (clonados, sin ejecutar):

| Configuración (mismo PDF) | Pico de memoria | Texto por página (caracteres) |
|---------------------------|-----------------|-------------------------------|
| docling, documento entero | > 3 GiB (aborta a los 22 s) | — |
| + `MALLOC_ARENA_MAX=2` + `OMP_NUM_THREADS=2` | 2,6 GiB | — |
| + mimalloc (como docling-serve) | 2,5 GiB | — |
| `queue_max_size` 2 / 4 | 2,3 / 2,7 GiB | — |
| docling, una página por conversión | 1,27 GiB | `[96, 0, 11269, 12541, 0, 33, 9097, 3139, 14769]` |
| texto nativo (pypdfium2) | 27 MiB, instantáneo | `[3959, 3725, 5053, 5704, 3058, 2646, 4006, 3343, 6527]` |

Con docling, páginas completas salían casi vacías y otras duplicadas: evidencia perdida sin aviso. Un conversor por
PDF (primer arreglo) no era la causa; el consumo crece con las páginas en vuelo del pipeline (docling-serve lo mitiga con
mimalloc y un presupuesto de memoria mayor).

## Decisión

- **Motor por defecto: texto nativo con pypdfium2**, una página a la vez. Las páginas con menos de 80 caracteres se
  rasterizan (escala 2,5) y pasan por Tesseract en español (`tesseract … -l spa --psm 3`), una por vez.
- docling queda como motor **opcional** (`INVESTIGACION_PDF_MOTOR=docling`), siempre una página por conversión y sin
  TableFormer, para cuando haga falta la estructura de tablas (fase 4). No se usa por defecto.
- La imagen de `extract` precarga mimalloc y fija `OMP_NUM_THREADS=2` (acota el consumo de GLiNER).
- Prueba de regresión: la cobertura por página debe igualar la del texto nativo (`tests/test_extraer_pdf.py`).

## Consecuencias

- La extracción de PDF digital deja de depender de modelos: rápida, determinista y verificable contra el original.
- El verificador de citas sigue trabajando sobre texto lineal; las tablas salen en el orden del flujo del PDF, no como
  celdas. Una cita que cruce celdas en otro orden quedará en `revisar`.
- El OCR solo se probó con un doble en las pruebas; falta probarlo con un PDF escaneado real en el cluster.
- Los PDF con imágenes que contienen texto (infografías) no pasan por OCR si la página ya tiene ≥ 80 caracteres.
