import pytest

from app.extraction import run_extraction
from app.extraction.base import (
    CorruptedFileError,
    EncryptedDocumentError,
    ExtractionContext,
    LimitExceededError,
)
from app.extraction.enrich import extract_entities


def ctx(**options):
    return ExtractionContext(options={"language": "eng+spa", "ocr_mode": "auto", **options})


def test_born_digital_pdf_uses_text_layer(sample):
    result = run_extraction(sample("invoice_digital.pdf"), "pdf", ctx())
    assert "INV-2026-0917" in result.text
    assert all(p.method == "text_layer" for p in result.pages)
    assert result.metadata["pdf"]["title"] == "Invoice INV-2026-0917"
    assert "billing@universidad.example.mx" in result.metadata["entities"]["emails"]


@pytest.mark.parametrize("engine", ["rapidocr", "tesseract"])
def test_scanned_pdf_is_ocrd(sample, engine):
    result = run_extraction(sample("scanned_letter.pdf"), "pdf", ctx(ocr_engine=engine))
    assert [p.method for p in result.pages] == [f"ocr:{engine}"] * 3
    assert "reconocimiento óptico" in result.text.lower()
    assert "INV-2026-0917" in result.text
    assert result.metadata["stats"]["mean_ocr_confidence"] > 80


def test_mixed_pdf_only_ocrs_scanned_pages(sample):
    result = run_extraction(sample("mixed_text_and_scan.pdf"), "pdf", ctx())
    assert result.metadata["ocr_pages"] == [2]
    assert any("Mixed document" in w for w in result.warnings)


def test_ocr_mode_off_skips_ocr(sample):
    result = run_extraction(sample("scanned_letter.pdf"), "pdf", ctx(ocr_mode="off"))
    assert result.text.strip() == ""
    assert result.warnings


@pytest.mark.parametrize("engine", ["rapidocr", "tesseract"])
def test_rotated_image_is_corrected(sample, engine):
    result = run_extraction(sample("rotated_90.png"), "image", ctx(ocr_engine=engine))
    assert "Invoice" in result.text
    assert any("rotated" in w for w in result.warnings)


def test_latin1_text(sample):
    result = run_extraction(sample("notes_latin1.txt"), "text", ctx())
    assert "digitalización" in result.text
    assert result.metadata["language"]["code"] == "es"


def test_html_strips_scripts(sample):
    result = run_extraction(sample("article.html"), "html", ctx())
    assert "console.log" not in result.text
    assert result.metadata["html"]["title"] == "Async pipelines"


def test_office_documents(sample):
    docx = run_extraction(sample("report.docx"), "docx", ctx())
    assert "Documents\t12,400\t18,950" in docx.text
    pptx = run_extraction(sample("slides.pptx"), "pptx", ctx())
    assert len(pptx.pages) == 2 and "[Notes]" in pptx.text
    xlsx = run_extraction(sample("budget.xlsx"), "xlsx", ctx())
    assert [p.text.splitlines()[0] for p in xlsx.pages] == ["# Budget", "# Notes"]


@pytest.mark.parametrize(
    "name, kind, error",
    [
        ("edge_cases/garbage.pdf", "pdf", CorruptedFileError),
        ("edge_cases/encrypted.pdf", "pdf", EncryptedDocumentError),
        ("edge_cases/truncated.jpg", "image", CorruptedFileError),
        ("edge_cases/huge_400mp.png", "image", LimitExceededError),
    ],
)
def test_bad_inputs_raise_typed_errors(sample, name, kind, error):
    with pytest.raises(error) as info:
        run_extraction(sample(name), kind, ctx())
    assert info.value.retryable is False


def test_truncated_pdf_is_repaired_with_warning(sample):
    result = run_extraction(sample("edge_cases/corrupted.pdf"), "pdf", ctx())
    assert result.metadata["pdf"]["repaired"] is True
    assert any("repaired" in w for w in result.warnings)


def test_entities():
    found = extract_entities("Pay $1,150.00 MXN to ops@example.com by 2026-10-01, call +52 999 123 4567. https://x.io/a")
    assert found["emails"] == ["ops@example.com"]
    assert found["dates"] == ["2026-10-01"]
    assert found["amounts"] == ["$1,150.00"]
    assert found["phone_numbers"] == ["+52 999 123 4567"]
    assert found["urls"] == ["https://x.io/a"]
    assert extract_entities("Phone:+529991234567")["phone_numbers"] == ["+529991234567"]
