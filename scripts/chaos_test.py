"""Failure-injection tests against the running docker compose stack.

    python3 scripts/chaos_test.py            # all scenarios
    python3 scripts/chaos_test.py worker_kill stack_kill

Requires only the Python standard library and the docker CLI. Writes docs/chaos_results.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:8000"
SAMPLES = ROOT / "samples"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


# ------------------------------------------------------------------ helpers


def compose(*args: str, check: bool = True) -> str:
    out = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {out.stderr}")
    return out.stdout


def request(method: str, path: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(API + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def upload(path: Path, **fields) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in {"force": "true", **fields}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode() + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return request("POST", "/v1/documents", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def job(job_id: str) -> dict:
    return request("GET", f"/v1/jobs/{job_id}")[1]


def events(job_id: str) -> list[str]:
    return [e["event"] for e in request("GET", f"/v1/jobs/{job_id}/events")[1]]


def wait_api(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if request("GET", "/health", timeout=3)[0] == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("API did not come back")


def wait_for(job_id: str, predicate, timeout: float = 400, poll: float = 1.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = job(job_id)
            if predicate(last):
                return last
        except Exception:
            pass  # API may be restarting
        time.sleep(poll)
    raise TimeoutError(f"timeout waiting on job {job_id}; last state: {last and (last['status'], last['stage'])}")


def wait_terminal(job_id: str, timeout: float = 400) -> dict:
    return wait_for(job_id, lambda j: j["status"] in TERMINAL, timeout)


def log(msg: str) -> None:
    print(f"  [{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------ scenarios


def worker_kill() -> dict:
    """SIGKILL the OCR worker container while it is in the middle of a 30-page scanned PDF."""
    _, j = upload(SAMPLES / "scanned_report_30p.pdf")
    log(f"uploaded 30-page scan, job {j['id'][:8]}")
    running = wait_for(j["id"], lambda s: s["status"] == "PROCESSING" and s["progress"] > 20, timeout=180)
    log(f"processing at {running['progress']:.0f}% ({running['stage_detail']}); SIGKILL worker-heavy")
    t0 = time.time()
    compose("kill", "-s", "SIGKILL", "worker-heavy")
    time.sleep(3)
    compose("start", "worker-heavy")
    log("worker-heavy restarted")
    final = wait_terminal(j["id"], timeout=600)
    ev = events(j["id"])
    return {
        "expect": "job re-queued after heartbeat timeout and SUCCEEDED on attempt 2",
        "ok": final["status"] == "SUCCEEDED" and final["attempts"] == 2 and "worker_lost" in ev,
        "observed": f"{final['status']} after {final['attempts']} attempts; recovered+finished {time.time() - t0:.0f}s after kill; "
                    f"events: {' → '.join(ev)}",
    }


def stack_kill() -> dict:
    """Kill every container (including Postgres, Redis, MinIO) with jobs queued and running, then bring it back."""
    ids = []
    for name in ("scanned_report_30p.pdf", "scanned_letter.pdf", "mixed_text_and_scan.pdf", "report.docx", "invoice_photo.jpg"):
        ids.append(upload(SAMPLES / name)[1]["id"])
    wait_for(ids[0], lambda s: s["status"] == "PROCESSING" and s["progress"] > 10, timeout=180)
    before = {i[:8]: job(i)["status"] for i in ids}
    log(f"states before kill: {before}; killing the whole stack")
    compose("kill")
    time.sleep(3)
    compose("up", "-d")
    wait_api()
    log("stack is back up")
    finals = [wait_terminal(i, timeout=900) for i in ids]
    return {
        "expect": "every job (queued, running, finished) reaches SUCCEEDED after a full restart",
        "ok": all(f["status"] == "SUCCEEDED" for f in finals),
        "observed": "; ".join(f"{f['document']['filename']}: {before[f['id'][:8]]}→{f['status']} (attempts {f['attempts']})"
                              for f in finals),
    }


def broker_data_loss() -> dict:
    """Workers down, jobs queued, then Redis loses every message (FLUSHALL)."""
    compose("stop", "worker-light", "worker-heavy")
    ids = [upload(SAMPLES / n)[1]["id"] for n in ("report.docx", "invoice_scan.png", "article.html")]
    log(f"{len(ids)} jobs queued with no workers; FLUSHALL on redis")
    compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")
    compose("start", "worker-light", "worker-heavy")
    t0 = time.time()
    finals = [wait_terminal(i, timeout=300) for i in ids]
    recovered = all("message_missing" in events(i) for i in ids)
    return {
        "expect": "sweeper detects jobs missing from the broker and re-dispatches them",
        "ok": all(f["status"] == "SUCCEEDED" for f in finals) and recovered,
        "observed": f"{[f['status'] for f in finals]} in {time.time() - t0:.0f}s; message_missing events: {recovered}",
    }


def broker_down_on_upload() -> dict:
    """Redis is down while the user uploads."""
    compose("stop", "redis")
    status, j = upload(SAMPLES / "readme.md")
    log(f"upload with redis down → HTTP {status}, job status {j.get('status')}")
    time.sleep(3)
    compose("start", "redis")
    final = wait_terminal(j["id"], timeout=300)
    ev = events(j["id"])
    return {
        "expect": "upload still accepted (202); job dispatched automatically when the broker returns",
        "ok": status == 202 and final["status"] == "SUCCEEDED" and "dispatch_failed" in ev,
        "observed": f"HTTP {status}; final {final['status']}; events: {' → '.join(ev)}",
    }


def storage_down_during_processing() -> dict:
    """Object storage becomes unreachable between upload and processing: transient error → retry with backoff."""
    network = "docintel_default"
    container = compose("ps", "-q", "minio").strip()
    compose("stop", "worker-light")
    _, j = upload(SAMPLES / "report.docx")
    # Cut MinIO off the network (stopping it would be undone by `compose start`, which starts dependencies).
    subprocess.run(["docker", "network", "disconnect", network, container], check=True, capture_output=True)
    try:
        compose("start", "worker-light")
        retrying = wait_for(j["id"], lambda s: s["status"] == "RETRYING", timeout=120)
        log(f"job is RETRYING ({retrying['stage_detail']})")
        status_during, body = upload(SAMPLES / "prices.csv")
        log(f"upload during outage → HTTP {status_during}; reconnecting MinIO")
    finally:
        subprocess.run(["docker", "network", "connect", "--alias", "minio", network, container],
                       check=True, capture_output=True)
    final = wait_terminal(j["id"], timeout=300)
    return {
        "expect": "job goes RETRYING with backoff, then SUCCEEDED; uploads during the outage get 503 STORAGE_UNAVAILABLE",
        "ok": final["status"] == "SUCCEEDED" and final["attempts"] >= 2 and status_during == 503,
        "observed": f"upload during outage → HTTP {status_during} {body['error']['code'] if body and 'error' in body else ''}; "
                    f"job {final['status']} after {final['attempts']} attempts; events: {' → '.join(events(j['id']))}",
    }


def database_down() -> dict:
    """Postgres is down: the API answers with a clear 503 instead of hanging or 500."""
    compose("stop", "postgres")
    time.sleep(2)
    status, body = request("GET", "/v1/jobs")
    sys_status, sys_body = request("GET", "/v1/system")
    compose("start", "postgres")
    time.sleep(5)
    after, _ = request("GET", "/v1/jobs")
    return {
        "expect": "HTTP 503 DATABASE_UNAVAILABLE while down, /v1/system reports degraded, recovers automatically",
        "ok": status == 503 and sys_status == 503 and after == 200,
        "observed": f"/v1/jobs → {status} {body and body.get('error', {}).get('code')}; /v1/system → {sys_status} "
                    f"{sys_body.get('messages')}; after restart → {after}",
    }


def cancel_running() -> dict:
    _, j = upload(SAMPLES / "scanned_report_30p.pdf")
    wait_for(j["id"], lambda s: s["status"] == "PROCESSING" and s["progress"] > 10, timeout=180)
    request("POST", f"/v1/jobs/{j['id']}/cancel")
    final = wait_terminal(j["id"], timeout=60)
    return {
        "expect": "a running job stops at the next page and ends CANCELLED",
        "ok": final["status"] == "CANCELLED",
        "observed": f"{final['status']} at {final['progress']:.0f}%",
    }


def rejected_inputs() -> dict:
    edge = SAMPLES / "edge_cases"
    expected = {"program.exe": 415, "archive.zip": 415, "empty.txt": 400, "binary_named_as.txt": 415}
    got = {name: upload(edge / name)[0] for name in expected}
    failing = {"corrupted.pdf": "SUCCEEDED", "garbage.pdf": "FAILED", "truncated.jpg": "FAILED",
               "encrypted.pdf": "FAILED", "huge_400mp.png": "FAILED"}
    finals = {name: wait_terminal(upload(edge / name)[1]["id"], timeout=120) for name in failing}
    return {
        "expect": "unsupported/empty → 4xx at upload; damaged files → terminal state with explicit error code (repairable PDFs succeed with a warning)",
        "ok": got == expected and all(finals[n]["status"] == s for n, s in failing.items()),
        "observed": "; ".join(f"{n} → HTTP {c}" for n, c in got.items()) + "; "
                    + "; ".join(f"{n} → {f['status']} {f['error']['code'] if f['error'] else '(warnings: ' + str(len(f['warnings'])) + ')'}"
                                for n, f in finals.items()),
    }


SCENARIOS = {
    "rejected_inputs": rejected_inputs,
    "worker_kill": worker_kill,
    "cancel_running": cancel_running,
    "broker_data_loss": broker_data_loss,
    "broker_down_on_upload": broker_down_on_upload,
    "storage_down_during_processing": storage_down_during_processing,
    "database_down": database_down,
    "stack_kill": stack_kill,
}


def main() -> int:
    selected = sys.argv[1:] or list(SCENARIOS)
    wait_api()
    results = []
    for name in selected:
        print(f"\n=== {name}: {SCENARIOS[name].__doc__ or ''}".rstrip(), flush=True)
        started = time.time()
        try:
            result = SCENARIOS[name]()
        except Exception as exc:
            result = {"expect": "", "ok": False, "observed": f"exception: {exc}"}
            compose("up", "-d", check=False)
            wait_api()
        result["name"], result["seconds"] = name, round(time.time() - started)
        print(f"  {'PASS' if result['ok'] else 'FAIL'} ({result['seconds']}s): {result['observed']}", flush=True)
        results.append(result)

    out = ROOT / "docs" / "chaos_results.md"
    out.parent.mkdir(exist_ok=True)
    lines = [f"# Chaos test results ({time.strftime('%Y-%m-%d %H:%M')})", "",
             "| Scenario | Result | Expected | Observed | Duration |", "|---|---|---|---|---|"]
    for r in results:
        cell = lambda s: str(s).replace("|", "\\|")
        lines.append(f"| `{r['name']}` | {'✅ PASS' if r['ok'] else '❌ FAIL'} | {cell(r['expect'])} | {cell(r['observed'])} | {r['seconds']}s |")
    out.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {out.relative_to(ROOT)}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
