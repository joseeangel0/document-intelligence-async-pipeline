# Document Intelligence — Asynchronous Pipeline (U1T02)

[![CI](https://github.com/joseeangel0/document-intelligence-async-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/joseeangel0/document-intelligence-async-pipeline/actions/workflows/ci.yml)

This service turns the files companies handle every day (scanned invoices, PDFs, Word, Excel, PowerPoint, HTML, e-mails with
attachments) into **LLM-ready content**: plain text, structured **Markdown** and **retrieval chunks** that keep their page and section.
It runs asynchronously: push a document, get a job id back immediately, then poll or receive a webhook.

Built with FastAPI, Celery on Redis, PostgreSQL, MinIO (S3) and on-device models (RapidOCR, Tesseract, optionally Docling).
It uses the **claim-check pattern**: files live in object storage and only a job id travels through the queue.

```bash
docker compose up --build                         # the whole stack, one command
docker compose --profile layout up -d --build     # optional: high-fidelity pipeline for tables in scans (Docling)
```

| What | URL |
|---|---|
| Upload UI and results (Markdown / text / chunks) | http://localhost:8000 |
| Monitoring dashboard | http://localhost:8000/dashboard |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Celery Flower | http://localhost:5555 |
| MinIO console (user `docintel` / `docintel-secret`) | http://localhost:9001 |

The report (architecture diagrams, product, benchmarks, test evidence, trade-offs) is in `report/U1T02_report.pdf`.
The requirement-by-requirement verification record is in `docs/VERIFICATION.md`.

## Try it

```bash
# 1. Upload (202 + job id; 200 if the identical file was already processed with the same options)
curl -F file=@samples/contract_structured.pdf http://localhost:8000/v1/documents

# 2. Track progress
curl http://localhost:8000/v1/jobs/<id>            # status, stage, progress %, tokens, chunks, warnings, error
curl http://localhost:8000/v1/jobs/<id>/events     # human-readable timeline

# 3. Results
curl http://localhost:8000/v1/jobs/<id>/markdown   # LLM-ready Markdown (headings, lists, tables, page markers)
curl http://localhost:8000/v1/jobs/<id>/chunks     # retrieval chunks with page range, heading path, token count
curl "http://localhost:8000/v1/jobs/<id>/chunks?format=jsonl&download=true" -o chunks.jsonl
curl http://localhost:8000/v1/jobs/<id>/text       # plain text
curl "http://localhost:8000/v1/jobs/<id>/result?include_chunks=true"   # everything in one JSON
```

Upload form fields (all optional except `file`):

| Field | Default | Purpose |
|---|---|---|
| `language` | `eng+spa` | OCR languages (Tesseract codes, `+`-joined) |
| `ocr_mode` | `auto` | `auto` = OCR only pages without a text layer · `force` · `off` |
| `ocr_engine` | `rapidocr` | `rapidocr` (most accurate) or `tesseract` (≈3× faster, best on clean scans) |
| `pipeline` | `standard` | `layout` = Docling layout + table models for PDFs/images (rebuilds tables in scans; ~8 s/page; needs the `layout` profile) |
| `chunk_size_tokens` | `512` | chunk budget in cl100k tokens (64–4096) |
| `chunk_overlap_tokens` | `64` | overlap between consecutive prose chunks (< chunk size) |
| `extract_entities` | `true` | emails, URLs, dates, amounts, phone numbers |
| `force` | `false` | re-process even if the same bytes + options were already processed |
| `client_reference` | – | your own id, searchable via `GET /v1/jobs?client_reference=` |
| `callback_url` | – | webhook POSTed when the job finishes |

Other endpoints: `GET /v1/jobs` (filters: status, kind, q, client_reference), `POST /v1/jobs/{id}/cancel`,
`POST /v1/jobs/{id}/retry`, `GET /v1/jobs/{id}/document`, `GET /v1/formats`, `GET /v1/system`, `GET /v1/stats`, `GET /health`.

**Supported formats** (detected from content, not extension):
- **PDF:** digital, scanned or mixed.
- **Images:** PNG, JPG, TIFF, WebP, BMP, GIF.
- **Text:** TXT, MD, CSV, TSV, LOG, JSON and XML, in any encoding.
- **Other:** HTML, DOCX, PPTX, XLSX (pictures inside Office files are OCR'd), RTF, and **EML** (the body plus every supported attachment).

**Limits:** 50 MB per upload · 500 PDF pages · 80 MP per image · 300 MB uncompressed Office archive ·
15 min processing · 3 attempts · 24 h in queue. All of them are configurable via environment variables (`app/config.py`, `.env.example`).

## Key decisions (evidence in the report)

- **Claim check.** Files go to MinIO, content-addressed by SHA-256. The broker message is only `{"job_id"}`, published *after* the DB commit.
- **Source of truth.** PostgreSQL holds `documents`, `jobs` and `job_events`. Celery results are disabled, so Redis is transport only.
- **Queues:**
  - `heavy` for PDFs, images, e-mail and Office files with pictures;
  - `light` for text and plain Office files;
  - `layout` for the opt-in Docling pipeline.

  A burst of scans never delays a text file: in the load test, light-queue wait stayed below 1 s at p95.
- **Benchmark A (OCR and text layers, 53 samples):**
  - RapidOCR is the default engine, with macro CER 0.001 against 0.214 for raw Tesseract.
  - Tesseract stays available as the fast option.
  - PyMuPDF reads PDF text layers; EasyOCR was rejected.
- **Benchmark B (structure for LLMs, 11 docs + 3 real IRS PDFs; the service itself was measured through its API):**
  - The standard pipeline reaches heading F1 1.00 and table F1 1.00 on digital PDFs and DOCX.
  - Scanned tables need the layout pipeline (table F1 1.00 against 0.00).
- **Chunking:**
  - Chunks follow document structure and a token budget.
  - Tables are never split mid-row, and the header repeats when a table is split.
  - Overlap is applied only between prose chunks.
  - Each chunk keeps its page range, heading path and character offsets.
- **No lost jobs, no endless jobs:**
  - `acks_late` plus an atomic claim, worker heartbeats, and a recovery sweeper (beat every 20 s and on every worker start);
  - retries with backoff for transient infrastructure errors;
  - typed, non-retryable errors for bad files;
  - max attempts, time limits, queue TTL, and cancellation that survives worker death.

## Tests

| Suite | Command | Checks |
|---|---|---|
| Unit | `docker compose run --rm --no-deps api pytest -q` | 47 |
| Unit (Docling pipeline) | `docker compose --profile layout run --rm --no-deps worker-layout pytest -q tests/test_layout_pipeline.py` | 3 |
| Integration (live stack, HTTP only) | `docker compose --profile test run --rm tests` | 40 |
| Chaos (failure injection) | `python3 scripts/chaos_test.py` → `docs/chaos_results.md` | 13 scenarios |
| Load (burst) | `python3 scripts/load_test.py` → `docs/load_test_results.md` | 1 |
| Benchmarks | see `benchmark/README.md` | A: OCR/text layer · B: structure |

CI (`.github/workflows/ci.yml`) runs build → unit → `docker compose up --wait` → integration → 9 chaos scenarios → load test
on a clean x86_64 GitHub runner.

To regenerate the sample documents:
`docker compose run --rm --no-deps -v "$PWD/samples:/srv/samples" -v "$PWD/scripts:/srv/scripts" --user root api python scripts/make_samples.py`

## Layout

```
app/
  api/            FastAPI app (upload, tracking, results, stats), response schemas
  worker/         Celery app + tasks (processing, heartbeat, retries, recovery sweep, webhooks)
  extraction/     detection, PDF (+ layout → Markdown), image, text, Office, e-mail extractors,
                  OCR engines, Docling pipeline, chunking, enrichment
  static/         upload UI and dashboard (vanilla HTML/JS)
  jobs.py         dispatch-after-commit, atomic claim, recovery sweeper
  db.py           SQLAlchemy models + additive migrations
  storage.py      S3/MinIO client (claim-check payloads and results)
benchmark/        benchmark A (OCR/text layers) and B (structure), datasets, results, charts
scripts/          chaos_test.py, load_test.py, make_samples.py, jobs_table.py
samples/          demo documents + edge cases (corrupted, encrypted, bombs, disguised types)
tests/            unit tests; tests/integration = HTTP tests against the running stack
docs/             implementation plan, verification record, chaos and load results
report/           report source (HTML + SVG figures) and PDF
```

## Scaling knobs

- **More OCR processes:** `HEAVY_CONCURRENCY=4 OCR_THREADS=2 docker compose up -d`. RapidOCR peaks at ~1.1 GB per process, so raise the 4 GB memory limit to match.
- **More containers:** `docker compose up -d --scale worker-heavy=3`.
- **Layout worker threads:** `LAYOUT_THREADS`.
- **Recovery timings:** `STALE_HEARTBEAT_S`, `REDISPATCH_AFTER_S`, `MAX_ATTEMPTS`, `QUEUED_TTL_S`.
