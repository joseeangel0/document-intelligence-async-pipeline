# Document Intelligence — Asynchronous Pipeline (U1T02)

Push a document, get a job id immediately, and poll until its text is ready. Built with
FastAPI, Celery on Redis, PostgreSQL, MinIO (S3) and on-device OCR (RapidOCR / Tesseract). It uses the
**claim-check pattern**: files live in object storage and only a job id goes through the queue.

```bash
docker compose up --build        # the whole stack, one command
```

| What | URL |
|---|---|
| Upload UI and results | http://localhost:8000 |
| Monitoring dashboard | http://localhost:8000/dashboard |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Celery Flower | http://localhost:5555 |
| MinIO console (user `docintel` / `docintel-secret`) | http://localhost:9001 |

The report with architecture diagrams and the reasoning behind each design choice is in `report/U1T02_report.pdf`.

## Try it

```bash
# 1. Upload (returns 202 + job id; 200 if the identical file was already processed)
curl -F file=@samples/scanned_letter.pdf -F language=eng+spa http://localhost:8000/v1/documents

# 2. Track progress
curl http://localhost:8000/v1/jobs/<id>            # status, stage, progress %, warnings, error
curl http://localhost:8000/v1/jobs/<id>/events     # human-readable timeline

# 3. Results
curl http://localhost:8000/v1/jobs/<id>/text       # plain text
curl http://localhost:8000/v1/jobs/<id>/result     # text + per-page info + metadata (JSON)
```

Upload form fields (all optional except `file`):

| Field | Default | Purpose |
|---|---|---|
| `language` | `eng+spa` | OCR languages (Tesseract codes, `+`-joined) |
| `ocr_mode` | `auto` | `auto` = OCR only pages without a text layer · `force` · `off` |
| `ocr_engine` | `rapidocr` | `rapidocr` (most accurate) or `tesseract` (≈3× faster, best on clean scans) |
| `extract_entities` | `true` | emails, URLs, dates, amounts, phone numbers |
| `force` | `false` | re-process even if the same bytes + options were already processed |
| `client_reference` | – | your own id, searchable via `GET /v1/jobs?client_reference=` |
| `callback_url` | – | webhook POSTed when the job finishes |

Other endpoints: `GET /v1/jobs` (filters: status, kind, q, client_reference), `POST /v1/jobs/{id}/cancel`,
`POST /v1/jobs/{id}/retry`, `GET /v1/jobs/{id}/document`, `GET /v1/formats`, `GET /v1/system`, `GET /v1/stats`, `GET /health`.

**Supported formats:** PDF (text layer + automatic OCR of scanned pages), PNG/JPG/TIFF/WebP/BMP/GIF,
TXT/MD/CSV/TSV/LOG/JSON/XML (any encoding), HTML, DOCX, PPTX, XLSX and RTF. The type is detected from
the file's content (magic bytes), not its extension.

**Limits:** 50 MB per upload · 500 PDF pages · 80 MP per image · 300 MB uncompressed Office archive ·
15 min processing · 3 attempts. All of them can be changed with environment variables (`app/config.py`).

## Key decisions (details and evidence in the report)

- **Claim check.** Files go to MinIO (content-addressed by SHA-256). The broker message is only `{"job_id"}`, and it is published *after* the DB commit.
- **Source of truth.** PostgreSQL holds `documents`, `jobs` and `job_events`. Celery results are disabled, so Redis is transport only.
- **Two queues.** `heavy` (PDF and images, may OCR) and `light` (text and office formats), so OCR bursts never delay cheap jobs.
- **Parsers were chosen by benchmark** (`benchmark/`, 53 samples with ground truth):
  - PyMuPDF `sort=True` for PDF text layers
  - RapidOCR PP-OCRv6 as the default OCR (macro CER 0.001 vs 0.214 for raw Tesseract, at ≈0.9 s/page)
  - Tesseract as the fast option
  - EasyOCR rejected (7× slower, 6.4 GB)
- **No lost jobs.**
  - `acks_late` and an atomic, idempotent claim
  - worker heartbeats
  - a recovery sweeper (beat every 20 s and on every worker start) that re-queues jobs whose worker died, re-dispatches jobs missing from Redis, and expires jobs stuck for 24 h
  - retries with backoff for transient storage/DB errors, and typed non-retryable errors for bad files

## Layout

```
app/
  api/            FastAPI app (upload, tracking, results, stats), response schemas
  worker/         Celery app + tasks (processing, recovery sweeper, webhooks)
  extraction/     format detection, PDF/image/text/office extractors, OCR engines, enrichment
  static/         upload UI and dashboard (vanilla HTML/JS)
  jobs.py         job lifecycle: dispatch, claim, recovery sweep
  db.py           SQLAlchemy models: documents, jobs, job_events
  storage.py      S3/MinIO client (claim-check payloads)
benchmark/        parser & OCR benchmark (dataset generator, runner, results)
scripts/          make_samples.py, chaos_test.py, jobs_table.py
samples/          demo documents + edge cases (corrupted, encrypted, bombs, disguised types)
tests/            pytest unit tests (format detection, extraction)
report/           report source (HTML) and PDF
```

## Tests

```bash
# unit tests (inside the image)
docker compose run --rm --no-deps api pytest -q

# failure injection against the running stack: kills workers mid-job, kills the whole stack,
# flushes Redis, takes Redis/MinIO/Postgres offline, cancels running jobs, uploads malformed files
python3 scripts/chaos_test.py          # writes docs/chaos_results.md

# parser/OCR benchmark (separate image, see benchmark/README.md)
```

To regenerate the sample documents:
`docker compose run --rm --no-deps -v "$PWD/samples:/srv/samples" -v "$PWD/scripts:/srv/scripts" --user root api python scripts/make_samples.py`

## Scaling knobs

`HEAVY_CONCURRENCY=4 OCR_THREADS=2 docker compose up -d` gives more OCR processes (RapidOCR peaks at about 1.1 GB per process, so raise the 4 GB memory limit to match), and
`docker compose up -d --scale worker-heavy=3` adds more OCR containers. Recovery timings are set with
`STALE_HEARTBEAT_S`, `REDISPATCH_AFTER_S`, `MAX_ATTEMPTS` and `QUEUED_TTL_S`.
