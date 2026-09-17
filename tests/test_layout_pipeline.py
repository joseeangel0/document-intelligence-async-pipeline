"""High-fidelity pipeline. Runs only in the `layout` image:
docker compose --profile layout run --rm --no-deps worker-layout pytest -q tests/test_layout_pipeline.py
"""

import re

import pytest

pytest.importorskip("docling")

from app.extraction import run_extraction  # noqa: E402
from app.extraction.base import ExtractionContext  # noqa: E402


def ctx(**options):
    return ExtractionContext(options={"pipeline": "layout", **options})


def test_scanned_table_is_rebuilt(sample):
    result = run_extraction(sample("contract_scanned.pdf"), "pdf", ctx())
    md = result.markdown
    assert result.pages[0].method == "layout:docling+ocr"
    rows = [line for line in md.splitlines() if line.startswith("|")]
    assert any(re.search(r"Service\s*\|\s*Unit\s*\|\s*Price", r) for r in rows)
    assert any(re.search(r"Storage\s*\|\s*GB-month\s*\|\s*5\.00", r) for r in rows)
    assert result.metadata["stats"]["tables"] >= 1
    assert "Payment terms" in md and result.chunks


def test_digital_pdf_and_image(sample):
    digital = run_extraction(sample("contract_structured.pdf"), "pdf", ctx())
    assert digital.pages[0].method == "layout:docling"
    assert "1,150.00" in digital.markdown
    image = run_extraction(sample("invoice_scan.png"), "image", ctx())
    assert "INV-2026-0917" in image.text


def test_other_formats_ignore_layout(sample):
    docx = run_extraction(sample("report.docx"), "docx", ctx())
    assert docx.pages[0].method.startswith("parser")
