# Plan de implementación — U1T02 al 100%

**Contexto del producto.** Document Intelligence es la capa que convierte los archivos del día a día de una empresa
(facturas escaneadas, contratos en PDF, Word, Excel, presentaciones, correos) en contenido que un LLM puede consumir.
Es la base de RAG, de los agentes y de cualquier caso de GenAI a escala. Por eso el objetivo no es solo "sacar texto
plano": hay que entregar texto **fiel, estructurado, trazable (página y sección) y listo para indexar**, con un pipeline
asíncrono que no pierda documentos.

## 1. Auditoría del enunciado contra el estado actual

| # | Requisito del enunciado | Estado antes de este plan | Brecha |
|---|---|---|---|
| I | Aceptar la subida, almacenarla, encolar el trabajo y seguir el progreso de forma asíncrona | ✅ | – |
| I | El usuario obtiene el contenido como texto plano | ✅ `text.txt` | Para LLM hace falta estructura: Markdown con títulos y tablas, chunks con página y sección, conteo de tokens |
| 1 | Endpoint para subir documentos | ✅ `POST /v1/documents` | – |
| 1 | Aceptar al menos PDF, PNG/JPG y TXT | ✅ | Falta prueba de integración por formato contra el stack |
| 1 | Patrón claim-check | ✅ MinIO + id en Redis | – |
| 1 | Rechazar formatos no soportados | ✅ 415 | – |
| 1 | Formatos adicionales | ✅ DOCX, PPTX, XLSX, HTML, RTF, MD, CSV, JSON | Formato empresarial muy común sin soporte: correo `.eml`. Las imágenes embebidas en DOCX/PPTX (escaneos pegados en Word) no se leen |
| 1 | Parámetros adicionales en el payload | ✅ language, ocr_mode, ocr_engine, force, client_reference, callback_url, extract_entities | Faltan parámetros de salida para LLM: `chunk_size_tokens`, `chunk_overlap_tokens` |
| 1 | Límites de subida razonables | ✅ | Falta prueba automática del 413 |
| 2 | Endpoints para consultar progreso y resultados | ✅ | Faltan `/markdown` y `/chunks` |
| 2 | Capa de base de datos del ciclo de vida | ✅ documents, jobs, job_events | – |
| 3 | Mejores métodos para parsear a texto legible por máquina, con benchmark | ✅ OCR y capa de texto de PDF | El benchmark no mide **estructura** (tablas, títulos), no compara conversores "LLM-ready" (pymupdf4llm, Docling, MarkItDown) y usa datos casi todos sintéticos |
| 3 | Cómo y dónde guardar el texto extraído | ✅ MinIO + PostgreSQL | Falta guardar Markdown y chunks (JSONL) |
| 3 | Otra metadata útil | ✅ | Faltan tokens, esquema de títulos, número de tablas y procedencia por chunk |
| 3 | On-device y open source | ✅ | – |
| 4 | Redis + Celery | ✅ | – |
| 5 | Formatos no soportados y archivos corruptos | ✅ probado | – |
| 5 | Workers o stack entero muertos a mitad del proceso; ningún job perdido | ✅ probado | – |
| 5 | Ningún documento en estado no terminal para siempre | ⚠️ | **Bug:** job cancelado cuyo worker muere → el sweeper lo re-encola, el claim lo rechaza y queda QUEUED hasta la expiración (24 h). **Bug:** objeto borrado en MinIO → se reintenta como error transitorio. Sin prueba: poison pill (3 caídas), TIMEOUT, expiración por TTL |
| 5 | Advertencias, mensajes de error y estado | ✅ | Falta prueba del webhook |
| 6 | Flower | ✅ | – |
| 6 | Dashboard | ✅ | Agregar métricas LLM (tokens, chunks) |
| 6 | UI simple para subir y leer resultados | ✅ | Vistas de Markdown y chunks |
| E | Todo corre con un solo `docker compose` | ✅ probado en arm64 | Sin prueba en x86_64 (la máquina típica del evaluador) → CI en GitHub Actions |
| E | Reporte PDF: diagramas, información del producto, argumentos técnicos | ✅ | Portada sin autor; sin sección GenAI/RAG; sin matriz de verificación; captura desactualizada |

## 2. Fases

### F1 — Salidas listas para LLM (núcleo del cambio)
1. **Modelo de resultado.** Cada página guarda `text` y `markdown`. A nivel documento se producen `text.txt`, `document.md` y `chunks.jsonl`.
2. **Markdown por formato:**
   - **PDF digital:** `pymupdf4llm` (títulos por tamaño de fuente, negritas, listas, tablas con `find_tables`). Se valida en el benchmark (F3).
   - **Páginas escaneadas e imágenes:** líneas de OCR agrupadas en párrafos.
   - **DOCX:** estilos Heading → `#`, estilos de lista → `-`, tablas → tabla Markdown, en orden del documento.
   - **PPTX:** `## Slide N: título`, viñetas, tablas y notas.
   - **XLSX:** una tabla Markdown por hoja (con tope de filas; el texto completo sigue en `text`).
   - **HTML:** limpieza y luego `markdownify`.
   - **CSV/TSV:** tabla Markdown. **JSON/XML:** bloque de código. **MD:** tal cual.
   - **EML:** tabla de encabezados, cuerpo (HTML → Markdown) y lista de adjuntos.
3. **Chunking** por estructura, pensado para RAG:
   - se corta primero por títulos y luego por párrafos o filas, sin partir tablas si caben;
   - tamaño en tokens (`tiktoken` cl100k, cacheado en la imagen) con overlap;
   - cada chunk lleva `id`, `index`, `text`, `heading_path`, `page_start`/`page_end`, `char_start`/`char_end`, `token_count`, `kind` (text/table).
4. **Parámetros nuevos:** `chunk_size_tokens` (default 512, rango 64–4096) y `chunk_overlap_tokens` (default 64, menor que el tamaño).
5. **API:**
   - `GET /v1/jobs/{id}/markdown` (descargable);
   - `GET /v1/jobs/{id}/chunks` (JSON, o JSONL con `?format=jsonl`);
   - `/result` incluye `markdown` y el resumen de chunks.
6. **Metadata:** `token_count`, `chunk_count`, `outline` (títulos), `table_count`.

### F2 — Formatos empresariales
1. **`.eml`** con la librería estándar `email`. El cuerpo text/html se convierte a Markdown; los adjuntos se listan con nombre, tipo y tamaño.
2. **OCR de imágenes embebidas** en DOCX y PPTX (respeta `ocr_mode`; ignora imágenes pequeñas o logos). Cubre el caso común de escaneos pegados en Word.

### F3 — Benchmark ampliado (evidencia para F1)
1. **Dataset con estructura conocida:**
   - PDFs digitales con títulos H1/H2, listas y tablas;
   - una tabla escaneada;
   - DOCX con títulos y tablas.
2. **Métricas:** recall de títulos, F1 de celdas de tabla, CER sobre el texto sin marcado, s/página, RAM pico y tamaño de instalación.
3. **Candidatos:**
   - PyMuPDF plano (línea base);
   - `pymupdf4llm`;
   - **Docling** (IBM, MIT, modelos de layout y TableFormer);
   - **MarkItDown** (Microsoft, MIT);
   - **el propio servicio de punta a punta vía API** (`docintel`).
4. **Documentos reales:** agregar un conjunto pequeño con licencia clara, o documentar por qué no se incluyó.
5. **Decisión basada en datos:** qué conversor usar por defecto y si Docling se ofrece como modo opcional de alta fidelidad.

### F4 — Robustez (bugs detectados en la auditoría)
1. El sweeper marca CANCELLED los jobs con `cancel_requested` que perdieron su worker, en vez de re-encolarlos.
2. Objeto ausente en storage (`NoSuchKey`) → `DOCUMENT_MISSING`, sin reintentos.
3. `extra_hosts: host.docker.internal:host-gateway` para que los webhooks a la máquina anfitriona funcionen también en Linux.

### F5 — Pruebas
1. **Unitarias:**
   - Markdown de cada formato;
   - chunker (límite de tokens, overlap, procedencia, tablas enteras);
   - EML;
   - OCR embebido;
   - reglas del sweeper.
2. **Integración** (`tests/integration`, contra el stack vivo):
   - cada formato → SUCCEEDED con text, markdown y chunks coherentes;
   - 415, 400, 413 y 422;
   - caché de duplicados;
   - cancelación y reintento manual;
   - eventos;
   - webhook recibido.
3. **Caos** (se suman a los 8 existentes):
   - poison pill (3 caídas → `FAILED WORKER_LOST`);
   - `TIMEOUT` con límite corto;
   - expiración por TTL;
   - cancelación + worker muerto → CANCELLED;
   - documento borrado en storage → `DOCUMENT_MISSING`.
4. **Carga:** ráfaga de más de 100 documentos mixtos. Verifica que todos terminan, mide throughput y comprueba que la cola `light` mantiene baja latencia mientras `heavy` está saturada.

### F6 — CI en GitHub Actions (x86_64)
1. Build de la imagen.
2. Unit tests.
3. `docker compose up`.
4. Pruebas de integración y un subconjunto de caos.
5. Resultado visible en el repo (badge), como evidencia de que corre en otra arquitectura y en una máquina limpia.

### F7 — UI y dashboard
1. **Pestañas Texto / Markdown / Chunks** en el detalle del job, con descargas `.md` y `.jsonl`.
2. **Dashboard:** tokens y chunks generados.

### F8 — Reporte y README
1. Portada con autor, curso y fecha.
2. Nueva sección "Por qué importa para GenAI": salidas para LLM y estrategia de chunking.
3. Benchmark de estructura.
4. Resultados de integración, caos, carga y CI.
5. Matriz de verificación requisito → implementación → prueba → resultado.
6. Capturas nuevas y README actualizado.

### F9 — Verificación final
1. Clon limpio.
2. `docker compose up --build` con volúmenes vacíos.
3. Suites unit, integración, caos y carga.
4. CI en verde.
5. `docs/VERIFICATION.md` con la evidencia.
6. Commit y push.
