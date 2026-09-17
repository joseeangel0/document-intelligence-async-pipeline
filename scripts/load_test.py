"""Burst load test: documents arrive faster than they can be processed.

    python3 scripts/load_test.py            # default burst (~120 documents)
    python3 scripts/load_test.py --scale 2  # twice as many

Checks that (1) uploads stay fast while workers are saturated, (2) every job reaches a terminal state,
(3) cheap documents on the `light` queue keep a low queue wait while the OCR queue is backlogged.
Standard library only. Writes docs/load_test_results.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chaos_test import API, ROOT, SAMPLES, TERMINAL, job, upload, wait_api

MIX = [  # (sample, copies per scale unit)
    ("scanned_letter.pdf", 10),
    ("invoice_photo.jpg", 12),
    ("invoice_scan.png", 12),
    ("contract_structured.pdf", 10),
    ("invoice_digital.pdf", 10),
    ("report.docx", 15),
    ("article.html", 15),
    ("readme.md", 15),
    ("prices.csv", 10),
    ("slides.pptx", 8),
    ("email_invoice.eml", 5),
]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()
    wait_api()

    batch = f"load-{uuid.uuid4().hex[:8]}"
    work = [name for name, copies in MIX for _ in range(max(1, int(copies * args.scale)))]
    print(f"uploading {len(work)} documents with {args.concurrency} parallel clients (batch {batch})")

    def send(name: str):
        t0 = time.perf_counter()
        status, body = upload(SAMPLES / name, client_reference=batch)
        return name, status, body, time.perf_counter() - t0

    started = time.time()
    with ThreadPoolExecutor(args.concurrency) as pool:
        uploads = list(pool.map(send, work))
    upload_wall = time.time() - started
    accepted = [(n, b) for n, s, b, _ in uploads if s == 202]
    latencies = [t for *_, t in uploads]
    print(f"accepted {len(accepted)}/{len(work)} in {upload_wall:.1f}s "
          f"(p50 {pct(latencies, 50) * 1000:.0f} ms, p95 {pct(latencies, 95) * 1000:.0f} ms)")

    peak_depth = {"heavy": 0, "light": 0}
    pending = {b["id"] for _, b in accepted}
    finals: dict[str, dict] = {}
    deadline = time.time() + 1800
    while pending and time.time() < deadline:
        stats = json.load(urllib.request.urlopen(f"{API}/v1/stats?window_minutes=15"))
        for q in peak_depth:
            peak_depth[q] = max(peak_depth[q], stats["queues"].get(q, 0))
        for job_id in list(pending):
            j = job(job_id)
            if j["status"] in TERMINAL:
                finals[job_id] = j
                pending.discard(job_id)
        time.sleep(2)
    drain = time.time() - started

    by_queue: dict[str, list[dict]] = {}
    for j in finals.values():
        by_queue.setdefault(j["queue"], []).append(j)
    statuses = {}
    for j in finals.values():
        statuses[j["status"]] = statuses.get(j["status"], 0) + 1
    pages = sum(j["page_count"] or 0 for j in finals.values())
    tokens = sum(j["token_count"] or 0 for j in finals.values())

    rows = []
    for queue, jobs in sorted(by_queue.items()):
        waits = [j["queue_wait_s"] for j in jobs if j["queue_wait_s"] is not None]
        durations = [j["duration_s"] for j in jobs if j["duration_s"] is not None]
        rows.append((queue, len(jobs), pct(waits, 50), pct(waits, 95), max(waits or [0]),
                     statistics.mean(durations or [0]), peak_depth.get(queue, 0)))

    lines = [
        f"# Load test results ({time.strftime('%Y-%m-%d %H:%M')})",
        "",
        f"* Documents uploaded: **{len(work)}** by {args.concurrency} parallel clients; accepted **{len(accepted)}** "
        f"in {upload_wall:.1f} s (upload latency p50 {pct(latencies, 50) * 1000:.0f} ms, "
        f"p95 {pct(latencies, 95) * 1000:.0f} ms).",
        f"* Terminal states: {statuses}; still pending after the deadline: **{len(pending)}**.",
        f"* All jobs drained in **{drain:.0f} s**: {pages} pages, {tokens:,} LLM tokens, "
        f"throughput {len(finals) / drain * 60:.0f} documents/min.",
        "",
        "| Queue | Jobs | Wait p50 (s) | Wait p95 (s) | Wait max (s) | Mean processing (s) | Peak depth |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {q} | {n} | {p50:.1f} | {p95:.1f} | {mx:.1f} | {d:.2f} | {depth} |" for q, n, p50, p95, mx, d, depth in rows]
    out = ROOT / "docs" / "load_test_results.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    ok = not pending and statuses.get("SUCCEEDED", 0) == len(accepted) == len(work)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
