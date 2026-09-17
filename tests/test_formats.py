import pytest

from app.extraction.formats import UnsupportedFormatError, detect_format


@pytest.mark.parametrize(
    "name, kind, queue",
    [
        ("invoice_digital.pdf", "pdf", "heavy"),
        ("invoice_scan.png", "image", "heavy"),
        ("invoice_photo.jpg", "image", "heavy"),
        ("notes_latin1.txt", "text", "light"),
        ("readme.md", "text", "light"),
        ("prices.csv", "text", "light"),
        ("article.html", "html", "light"),
        ("report.docx", "docx", "light"),
        ("slides.pptx", "pptx", "light"),
        ("budget.xlsx", "xlsx", "light"),
        ("memo.rtf", "rtf", "light"),
    ],
)
def test_detects_supported_formats(sample, name, kind, queue):
    fmt = detect_format(sample(name), name)
    assert (fmt.kind, fmt.queue) == (kind, queue)
    assert fmt.warnings == []


def test_content_wins_over_extension(sample):
    fmt = detect_format(sample("edge_cases/invoice_named_as.txt"), "invoice_named_as.txt")
    assert fmt.kind == "image"
    assert "does not match" in fmt.warnings[0]


def test_text_disguised_as_image(sample):
    fmt = detect_format(sample("edge_cases/text_named_as.png"), "text_named_as.png")
    assert fmt.kind == "text"
    assert fmt.warnings


@pytest.mark.parametrize("name", ["edge_cases/program.exe", "edge_cases/archive.zip", "edge_cases/binary_named_as.txt"])
def test_rejects_unsupported(sample, name):
    with pytest.raises(UnsupportedFormatError):
        detect_format(sample(name), name)
