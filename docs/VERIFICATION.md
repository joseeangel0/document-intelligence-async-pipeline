# Verificación — U1T02 contra el enunciado

Cada punto del enunciado (`U1T02.pdf`) tiene una implementación y al menos una prueba automática que corre contra
contenedores reales. Las pruebas se ejecutaron el 2026-09-16 en arm64 (OrbStack, perfil `layout` activo) y en CI sobre
x86_64 (GitHub Actions, checkout limpio).

## Resumen de suites

| Suite | Comando | Resultado |
|---|---|---|
| Unitarias (imagen app) | `docker compose run --rm --no-deps api pytest -q` | ✅ 47 pasan (1 skip: prueba de Docling fuera de su imagen) |
| Unitarias (imagen layout) | `docker compose --profile layout run --rm --no-deps worker-layout pytest -q tests/test_layout_pipeline.py tests/test_markdown_outputs.py tests/test_chunking.py` | ✅ 18 pasan |
| Integración por HTTP | `docker compose --profile test run --rm tests` | ✅ 40 pasan |
| Caos (inyección de fallos) | `python3 scripts/chaos_test.py` | ✅ 13/13 → [`chaos_results.md`](chaos_results.md) |
| Carga (ráfaga) | `python3 scripts/load_test.py` | ✅ 122/122 → [`load_test_results.md`](load_test_results.md) |
| Benchmark A (OCR y capa de texto) | ver `benchmark/README.md` | [`benchmark/results/summary.md`](../benchmark/results/summary.md) |
| Benchmark B (estructura para LLM) | ver `benchmark/README.md` | [`benchmark/results/structure/summary.md`](../benchmark/results/structure/summary.md) |
| CI x86_64 (checkout limpio) | `.github/workflows/ci.yml` | ✅ run 35184845357 (12 min): unit 47, integración 39 + 1 skip (perfil layout no se levanta en CI), caos 9/9, carga 59/59 |

## Matriz requisito → implementación → evidencia

| # | Requisito | Implementación | Evidencia | Estado |
|---|---|---|---|---|
| I | Aceptar, almacenar, encolar y dar seguimiento asíncrono | `POST /v1/documents` → 202 + job id; MinIO + PostgreSQL + Celery | `test_format_end_to_end` (17 formatos) | ✅ |
| I | Obtener el contenido como texto (y, para GenAI, Markdown y chunks) | `/text`, `/markdown`, `/chunks`, `/result` | `test_format_end_to_end` valida los 4 por formato | ✅ |
| 1 | Endpoint para subir documentos | `app/api/main.py::upload_document` | integración | ✅ |
| 1 | PDF, PNG/JPG, TXT como mínimo | detección por magic bytes (`app/extraction/formats.py`) | `test_format_end_to_end[invoice_digital.pdf, invoice_scan.png, invoice_photo.jpg, notes_latin1.txt…]` | ✅ |
| 1 | Patrón claim-check | payload en MinIO, mensaje `{"job_id"}` publicado tras el commit (`app/jobs.py::dispatch`) | integración + caos `broker_data_loss`, `broker_down_on_upload` | ✅ |
| 1 | Rechazar formatos no soportados | 415 con extensiones aceptadas; 400 vacío | `test_rejected_uploads`, caos `rejected_inputs` | ✅ |
| 1 | Formatos adicionales | HTML, DOCX, PPTX, XLSX, RTF, MD, CSV, TSV, JSON, XML, EML (con adjuntos), TIFF/WebP/BMP/GIF | `test_format_end_to_end`, `test_email_with_attachments` | ✅ |
| 1 | Parámetros adicionales útiles | language, ocr_mode, ocr_engine, pipeline, chunk_size_tokens, chunk_overlap_tokens, extract_entities, force, client_reference, callback_url | `test_invalid_options` (6 casos), `test_duplicate_upload_is_served_from_cache`, `test_webhook_is_delivered`, `test_listing_filters_and_not_found`, `test_chunk_options_are_applied` | ✅ |
| 1 | Límites de subida razonables | 50 MB antes de bufferizar, 500 páginas, 80 MP, 300 MB zip, 15 min, 3 intentos, TTL 24 h | `test_upload_limit_is_enforced_before_buffering`, `huge_400mp.png`, caos `processing_timeout`, `queue_expiry` | ✅ |
| 2 | Endpoints de seguimiento de progreso y resultados | `/v1/jobs`, `/v1/jobs/{id}`, `/events`, `/result`, `/text`, `/markdown`, `/chunks`, `/document`, `/cancel`, `/retry` | `test_result_not_ready_then_cancel_then_retry`, `test_original_document_download` | ✅ |
| 2 | Capa de base de datos del ciclo de vida | `documents`, `jobs`, `job_events` + migraciones aditivas (`app/db.py`) | todas las pruebas de integración leen el estado desde la BD | ✅ |
| 3 | Mejores métodos de parseo, con benchmark (no "elegí pytesseract") | pipeline híbrido por página; RapidOCR; PyMuPDF + análisis de layout; Docling opcional | Benchmark A (53 muestras, 5 motores OCR, 6 extractores PDF) y B (11 docs + 3 PDFs reales, 8 candidatos incluyendo el propio servicio por API) | ✅ |
| 3 | Cómo y dónde guardar el texto extraído | MinIO: `text.txt`, `document.md`, `chunks.jsonl`, `result.json`; PostgreSQL: resumen consultable | integración (`/text`, `/markdown`, `/chunks`, `/result`) | ✅ |
| 3 | Metadata adicional | idioma, entidades, tokens, chunks, tablas, outline, metadata PDF/imagen/Office/email, confianza OCR, tiempos | `test_structured_pdf_headings_list_table`, `test_entities`, `test_latin1_text` | ✅ |
| 3 | On-device y open source | modelos horneados en las imágenes; sin llamadas externas | CI sin credenciales; licencias en el reporte §5 | ✅ |
| 4 | Redis + Celery | colas heavy/light/layout/maintenance, acks_late, prefetch 1, beat | todos los escenarios de caos | ✅ |
| 5 | Formatos no soportados | 415 en la subida | `rejected_inputs` | ✅ |
| 5 | Archivos corruptos | errores tipados sin reintentos inútiles; reparación de PDF con advertencia | `test_bad_files_fail_with_explicit_error`, `test_truncated_pdf_is_repaired_with_warning` | ✅ |
| 5 | Workers muertos a mitad del proceso | heartbeat + sweeper + claim idempotente | caos `worker_kill` (SUCCEEDED en intento 2), `poison_pill` (FAILED WORKER_LOST tras 3) | ✅ |
| 5 | Stack completo muerto y reiniciado | volúmenes, AOF, sweep al arrancar cada worker | caos `stack_kill` (5/5 SUCCEEDED) | ✅ |
| 5 | Ningún job perdido | re-dispatch de mensajes faltantes, dispatch después del commit | caos `broker_data_loss`, `broker_down_on_upload` | ✅ |
| 5 | Ningún documento en estado no terminal para siempre | máx. intentos, límites de tiempo (incluso envueltos por ONNX), TTL de cola, cancelación con worker muerto, documento faltante no reintentable | caos `poison_pill`, `processing_timeout`, `queue_expiry`, `cancel_while_worker_dead`, `document_missing` | ✅ |
| 5 | Advertencias, errores y estado para el usuario | códigos de error, warnings, timeline, `/v1/system`, banners en UI | `test_warnings_are_reported`, caos `database_down`, `storage_down_during_processing` | ✅ |
| 6 | Flower | servicio `flower` :5555 | captura en el reporte | ✅ |
| 6 | Dashboard | `/dashboard` (salud, throughput, colas, fallos, recuperaciones, tokens) | `test_system_formats_stats_and_ui` + captura | ✅ |
| 6 | UI simple para subir y leer resultados | `/` con pestañas Markdown / texto / chunks y descargas | `test_system_formats_stats_and_ui` + captura | ✅ |
| E | Todo corre con un solo `docker compose` | `docker compose up --build` | CI en runner x86_64 limpio; prueba local con volúmenes vacíos | ✅ |
| E | Reporte PDF con diagramas, producto y argumentos | `report/U1T02_report.pdf` (arquitectura, secuencia, estados, flujo LLM, 2 benchmarks, verificación) | — | ✅ |

## Bugs encontrados por la propia verificación (y corregidos)

| Hallazgo | Cómo se detectó | Corrección | Prueba que lo cubre |
|---|---|---|---|
| El mensaje se publicaba antes del commit: un worker rápido lo descartaba | caos (primera corrida) | dispatch en `after_commit` | integración end-to-end, `broker_data_loss` |
| Un PUT vacío a MinIO desincronizaba la conexión (30 s de espera) | smoke test con `blank_page.pdf` | quitar `Expect: 100-continue` en cuerpos vacíos | `test_warnings_are_reported` (blank page) |
| Subida colgada hasta 90 s con MinIO inalcanzable | caos `storage_down_during_processing` | cliente S3 fail-fast en la ruta del request | mismo escenario |
| Job cancelado cuyo worker muere quedaba QUEUED hasta 24 h | auditoría del plan | el sweeper lo marca CANCELLED | caos `cancel_while_worker_dead` |
| Objeto borrado en storage se reintentaba como transitorio | auditoría del plan | `ObjectMissingError` → `DOCUMENT_MISSING` | caos `document_missing` |
| Límite de tiempo envuelto por ONNX Runtime se reintentaba 3× | caos `processing_timeout` | clasificación por cadena de causas y tiempo | caos `processing_timeout` |
| Viñetas sueltas, PDF a dos columnas cruzado, tablas sin bordes perdidas, títulos partidos | benchmark B (servicio por API) | fusión por línea base, XY-cut, detección de tablas sin reglas, fusión de títulos | benchmark B, `test_structured_pdf_headings_list_table` |

## Limitaciones conocidas (documentadas en el reporte §10)

- **Pipeline estándar en escaneos:** no reconstruye tablas (F1 0.00; con `pipeline=layout` es 1.00) y recupera títulos solo parcialmente (F1 0.62).
- **Pipeline `layout`:** cuesta unos 8 s por página y unos 3 GB de RAM. Es opcional y corre en un perfil aparte.
- **CI:** no corre el perfil `layout` (imagen de 4.5 GB). Sus pruebas se ejecutaron localmente.
- **Fuera de alcance del curso:** autenticación, cuotas, antivirus, retención y embeddings. Quedan descritos como trabajo futuro.
