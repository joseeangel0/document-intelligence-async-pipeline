"""End-to-end tests through the public HTTP API of the running compose stack."""

import http.server
import json
import re
import socket
import threading
import uuid

import pytest

FORMATS = [
    # sample, kind, queue, text that must be extracted, markdown that must be present
    ("invoice_digital.pdf", "pdf", "heavy", "INV-2026-0917", "Invoice No. INV-2026-0917"),
    ("contract_structured.pdf", "pdf", "heavy", "Payment terms", "| Service | Unit | Price (MXN) |"),
    ("scanned_letter.pdf", "pdf", "heavy", "reconocimiento", "<!-- page: 3 -->"),
    ("mixed_text_and_scan.pdf", "pdf", "heavy", "digitalización", "<!-- page: 2 -->"),
    ("invoice_scan.png", "image", "heavy", "INV-2026-0917", "INV-2026-0917"),
    ("invoice_photo.jpg", "image", "heavy", "2026-0917", "2026-0917"),
    ("rotated_90.png", "image", "heavy", "INV-2026-0917", "INV-2026-0917"),
    ("notes_latin1.txt", "text", "light", "digitalización", "digitalización"),
    ("readme.md", "text", "light", "claim-check", "# Sample"),
    ("prices.csv", "text", "light", "OCR pages", "| item | qty | price |"),
    ("article.html", "html", "light", "Async pipelines", "# Async pipelines"),
    ("report.docx", "docx", "light", "Quarterly report", "| Metric | Q2 | Q3 |"),
    ("contract_with_scan.docx", "docx", "heavy", "INV-2026-0917", "> **[Text in image]**"),
    ("slides.pptx", "pptx", "light", "Arquitectura", "## Slide 2: Arquitectura"),
    ("budget.xlsx", "xlsx", "light", "Storage", "## Sheet: Budget"),
    ("memo.rtf", "rtf", "light", "pipeline is ready", "pipeline is ready"),
    ("email_invoice.eml", "eml", "heavy", "Adjuntamos", "## Attachment: invoice_digital.pdf"),
]


@pytest.mark.parametrize("name, kind, queue, text_snippet, md_snippet", FORMATS, ids=[f[0] for f in FORMATS])
def test_format_end_to_end(client, upload, wait, name, kind, queue, text_snippet, md_snippet):
    res = upload(name)
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["status"] == "QUEUED" and job["document"]["kind"] == kind and job["queue"] == queue
    assert res.headers["location"] == f"/v1/jobs/{job['id']}"

    done = wait(job["id"])
    assert done["status"] == "SUCCEEDED", done
    assert done["progress"] == 100 and done["page_count"] >= 1 and done["token_count"] > 0 and done["chunk_count"] >= 1

    text = client.get(done["links"]["text"]).text
    markdown = client.get(done["links"]["markdown"]).text
    assert text_snippet.lower() in text.lower()
    assert md_snippet in markdown

    chunks = client.get(done["links"]["chunks"]).json()
    assert chunks["count"] == done["chunk_count"] == len(chunks["chunks"])
    assert all(c["token_count"] > 0 and c["page_start"] >= 1 for c in chunks["chunks"])
    jsonl = client.get(done["links"]["chunks"], params={"format": "jsonl"}).text.strip().splitlines()
    assert len(jsonl) == chunks["count"] and json.loads(jsonl[0])["job_id"] == job["id"]

    result = client.get(done["links"]["result"], params={"include_chunks": True}).json()
    assert result["markdown"] == markdown and len(result["chunks"]) == chunks["count"]
    assert result["metadata"]["stats"]["tokenizer"] == "tiktoken:cl100k_base"

    events = [e["event"] for e in client.get(done["links"]["events"]).json()]
    assert events[:2] == ["received", "dispatched"] and "started" in events and events[-1] == "succeeded"


@pytest.mark.parametrize(
    "filename, content, status, code",
    [
        ("program.exe", b"MZ\x90\x00" + bytes(2000), 415, "UNSUPPORTED_FORMAT"),
        ("archive.zip", None, 415, "UNSUPPORTED_FORMAT"),
        ("empty.txt", b"", 400, "EMPTY_FILE"),
    ],
)
def test_rejected_uploads(upload, filename, content, status, code):
    res = upload(f"edge_cases/{filename}" if content is None else None, content=content, filename=filename)
    assert res.status_code == status
    error = res.json()["error"]
    assert error["code"] == code and error["message"]
    if status == 415:
        assert ".pdf" in error["supported_extensions"]


def test_upload_limit_is_enforced_before_buffering(upload):
    res = upload(content=b"%PDF-1.7\n" + bytes(51 * 1024 * 1024), filename="huge.pdf")
    assert res.status_code == 413 and res.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.parametrize(
    "fields, code",
    [
        ({"language": "xyz"}, "INVALID_LANGUAGE"),
        ({"ocr_engine": "magic"}, "INVALID_OCR_ENGINE"),
        ({"chunk_size_tokens": 128, "chunk_overlap_tokens": 128}, "INVALID_CHUNKING"),
        ({"chunk_size_tokens": 10}, "INVALID_REQUEST"),
        ({"ocr_mode": "sometimes"}, "INVALID_REQUEST"),
        ({"callback_url": "ftp://example.com"}, "INVALID_CALLBACK_URL"),
    ],
)
def test_invalid_options(upload, fields, code):
    res = upload("readme.md", **fields)
    assert res.status_code == 422 and res.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "name, code",
    [("garbage.pdf", "CORRUPTED_FILE"), ("truncated.jpg", "CORRUPTED_FILE"),
     ("encrypted.pdf", "ENCRYPTED_DOCUMENT"), ("huge_400mp.png", "LIMIT_EXCEEDED")],
)
def test_bad_files_fail_with_explicit_error(client, upload, wait, name, code):
    job = wait(upload(f"edge_cases/{name}").json()["id"])
    assert job["status"] == "FAILED" and job["error"]["code"] == code and job["attempts"] == 1  # no pointless retries
    res = client.get(job["links"]["result"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "JOB_FAILED"


def test_warnings_are_reported(upload, wait):
    mismatch = upload("edge_cases/text_named_as.png").json()
    assert any("does not match its content" in w for w in mismatch["warnings"])
    repaired = wait(upload("edge_cases/corrupted.pdf").json()["id"])
    assert repaired["status"] == "SUCCEEDED" and any("repaired" in w for w in repaired["warnings"])
    blank = wait(upload("edge_cases/blank_page.pdf").json()["id"])
    assert blank["status"] == "SUCCEEDED" and any("No text" in w for w in blank["warnings"])


def test_duplicate_upload_is_served_from_cache(upload, wait):
    content = f"cache test {uuid.uuid4()}".encode()
    first = wait(upload(content=content, filename="dup.txt", force=False).json()["id"])
    second = upload(content=content, filename="dup-again.txt", force=False)
    assert second.status_code == 200
    assert second.json()["status"] == "SUCCEEDED" and second.json()["cached_from_job_id"] == first["id"]
    different_options = upload(content=content, filename="dup.txt", force=False, chunk_size_tokens=256)
    assert different_options.status_code == 202  # options are part of the cache key


def test_result_not_ready_then_cancel_then_retry(client, upload, wait):
    job = upload("scanned_report_30p.pdf").json()
    early = client.get(job["links"]["markdown"])
    assert early.status_code == 409 and early.json()["error"]["code"] == "RESULT_NOT_READY"
    wait(job["id"], lambda j: j["status"] in ("PROCESSING", "SUCCEEDED"), timeout=120)
    cancel = client.post(f"/v1/jobs/{job['id']}/cancel")
    assert cancel.status_code == 200
    cancelled = wait(job["id"], timeout=60)
    assert cancelled["status"] == "CANCELLED"
    assert client.post(f"/v1/jobs/{job['id']}/cancel").json()["error"]["code"] == "JOB_NOT_ACTIVE"
    retried = client.post(f"/v1/jobs/{job['id']}/retry")
    assert retried.status_code == 202 and retried.json()["status"] == "QUEUED"
    client.post(f"/v1/jobs/{job['id']}/cancel")  # don't spend a minute OCR'ing it again
    assert wait(job["id"], timeout=60)["status"] == "CANCELLED"


def test_webhook_is_delivered(upload, wait):
    received: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://{socket.gethostname()}:{server.server_port}/hook"
        job = upload("readme.md", callback_url=url, client_reference="webhook-test").json()
        wait(job["id"], lambda j: any(e["job"]["id"] == j["id"] for e in received), timeout=60)
        assert received[0]["event"] == "job.succeeded" and received[0]["job"]["client_reference"] == "webhook-test"
    finally:
        server.shutdown()


def test_listing_filters_and_not_found(client, upload, wait):
    ref = f"ref-{uuid.uuid4()}"
    job = wait(upload("prices.csv", client_reference=ref).json()["id"])
    listed = client.get("/v1/jobs", params={"client_reference": ref}).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == job["id"]
    assert client.get("/v1/jobs", params={"status": "SUCCEEDED", "kind": "text", "limit": 1}).json()["items"]
    missing = client.get(f"/v1/jobs/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_original_document_download(client, upload, wait):
    job = wait(upload("invoice_digital.pdf").json()["id"])
    res = client.get(job["links"]["document"])
    assert res.status_code == 200 and res.content.startswith(b"%PDF")


def test_system_formats_stats_and_ui(client):
    system = client.get("/v1/system").json()
    assert system["status"] == "ok" and system["components"]["workers"]["online"]
    formats = client.get("/v1/formats").json()
    assert {f["kind"] for f in formats["formats"]} >= {"pdf", "image", "text", "docx", "eml"}
    assert set(formats["ocr"]["engines"]) == {"rapidocr", "tesseract"}
    stats = client.get("/v1/stats", params={"window_minutes": 60}).json()
    assert {"status_counts", "window", "timeline", "workers", "queues"} <= set(stats)
    assert stats["window"]["tokens"] >= 0
    for path in ("/", "/dashboard", "/docs", "/openapi.json", "/health"):
        assert client.get(path).status_code == 200
    assert re.search(r"Document Intelligence", client.get("/").text)


def test_layout_pipeline_rebuilds_scanned_tables(client, upload, wait):
    if not client.get("/v1/formats").json()["pipelines"]["layout"]["online"]:
        pytest.skip("layout workers not running (docker compose --profile layout up -d)")
    job = upload("contract_scanned.pdf", pipeline="layout").json()
    assert job["queue"] == "layout"
    done = wait(job["id"], timeout=600)
    assert done["status"] == "SUCCEEDED"
    markdown = client.get(done["links"]["markdown"]).text
    assert re.search(r"\|\s*Storage\s*\|\s*GB-month\s*\|\s*5\.00\s*\|", markdown)
    standard = wait(upload("contract_scanned.pdf").json()["id"])
    assert "| Storage |" not in client.get(standard["links"]["markdown"]).text  # OCR lines only


def test_layout_pipeline_warns_for_formats_it_does_not_apply_to(upload):
    res = upload("readme.md", pipeline="layout").json()
    assert res["queue"] == "light" and any("applies to PDFs and images" in w for w in res["warnings"])
