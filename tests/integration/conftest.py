import os
import time
from pathlib import Path

import httpx
import pytest

API_URL = os.environ.get("API_URL", "http://localhost:8000")
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=API_URL, timeout=60) as c:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                if c.get("/v1/system").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            pytest.fail("stack not ready: /v1/system never returned 200")
        yield c


@pytest.fixture
def upload(client):
    def _upload(name: str | None = None, content: bytes | None = None, filename: str | None = None, **fields):
        data = content if content is not None else (SAMPLES / name).read_bytes()
        files = {"file": (filename or name, data)}
        form = {"force": "true", **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in fields.items()}}
        return client.post("/v1/documents", files=files, data=form)

    return _upload


@pytest.fixture
def wait(client):
    def _wait(job_id: str, predicate=lambda j: j["status"] in TERMINAL, timeout: float = 300) -> dict:
        deadline = time.time() + timeout
        job = None
        while time.time() < deadline:
            job = client.get(f"/v1/jobs/{job_id}").json()
            if predicate(job):
                return job
            time.sleep(0.5)
        pytest.fail(f"timeout waiting for job {job_id}: last state {job and (job['status'], job['stage'])}")

    return _wait
