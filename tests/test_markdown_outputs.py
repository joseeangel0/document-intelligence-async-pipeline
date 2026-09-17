import re

import pytest

from app.extraction import run_extraction
from app.extraction.base import ExtractionContext
from app.extraction.formats import detect_format, route_queue
from app.extraction.markdown import lines_to_markdown, table_to_markdown


def ctx(**options):
    return ExtractionContext(options={"language": "eng+spa", "ocr_mode": "auto", **options})


def md_tables(markdown: str) -> list[list[list[str]]]:
    tables, current = [], []
    for line in markdown.splitlines() + [""]:
        if line.startswith("|"):
            if not re.match(r"^\|[\s:|-]+\|$", line):
                current.append([c.strip() for c in line.strip("|").split("|")])
        elif current:
            tables.append(current)
            current = []
    return tables


def test_helpers():
    assert table_to_markdown([["a", "b|c"], ["1", None]]) == "| a | b\\|c |\n|---|---|\n| 1 |  |"
    assert lines_to_markdown("• one\n• two\n\n1) first") == "- one\n- two\n\n1. first"


def test_structured_pdf_headings_list_table(sample):
    result = run_extraction(sample("contract_structured.pdf"), "pdf", ctx())
    md = result.markdown
    assert re.search(r"^# Service Agreement$", md, re.M)
    assert re.search(r"^#{2,3} 2\. Payment terms$", md, re.M)
    assert "- Markdown with tables preserved" in md
    assert md_tables(md)[0] == [["Service", "Unit", "Price (MXN)"], ["OCR page", "page", "3.00"],
                                ["Storage", "GB-month", "5.00"], ["Support", "hour", "1,150.00"]]
    assert result.metadata["pdf"]["title"] == "Service Agreement"
    assert result.metadata["stats"]["tables"] == 1
    assert [h["title"] for h in result.metadata["outline"]][:2] == ["Service Agreement", "1. Scope of services"]
    assert result.chunks and result.chunks[0].heading_path[0] == "Service Agreement"


def test_docx_structure_and_embedded_scan_ocr(sample):
    path = sample("contract_with_scan.docx")
    fmt = detect_format(path, "contract_with_scan.docx")
    assert route_queue(fmt, path, {"ocr_mode": "auto"}) == "heavy"  # has a picture -> OCR pool
    assert route_queue(fmt, path, {"ocr_mode": "off"}) == "light"
    result = run_extraction(path, "docx", ctx())
    md = result.markdown
    assert md.startswith("# Service Agreement")
    assert "## 2. Payment terms" in md and "- Markdown with tables preserved" in md
    assert md_tables(md)[0][0] == ["Service", "Unit", "Price (MXN)"]
    assert "> **[Text in image]**" in md and "INV-2026-0917" in result.text
    assert result.metadata["docx"]["embedded_images_ocr"] == 1
    off = run_extraction(path, "docx", ctx(ocr_mode="off"))
    assert "INV-2026-0917" not in off.text and any("ocr_mode=off" in w for w in off.warnings)


def test_pptx_slides_and_xlsx_tables(sample):
    pptx = run_extraction(sample("slides.pptx"), "pptx", ctx())
    assert pptx.markdown.startswith("<!-- page: 1 -->\n\n## Slide 1: Document Intelligence")
    assert "**Speaker notes:**" in pptx.markdown
    xlsx = run_extraction(sample("budget.xlsx"), "xlsx", ctx())
    assert "## Sheet: Budget" in xlsx.markdown
    assert md_tables(xlsx.markdown)[0][0] == ["Concept", "Amount (MXN)"]


def test_html_csv_json_markdown(sample, tmp_path):
    html = run_extraction(sample("article.html"), "html", ctx())
    assert html.markdown.startswith("# Async pipelines") and "console.log" not in html.markdown
    csv_result = run_extraction(sample("prices.csv"), "text", ctx())
    assert md_tables(csv_result.markdown)[0][0] == ["item", "qty", "price"]
    json_file = tmp_path / "data.json"
    json_file.write_text('{"a": [1, 2]}')
    assert run_extraction(str(json_file), "text", ctx()).markdown.startswith("```json\n{\n  \"a\"")


def test_plain_text_is_escaped_not_reinterpreted(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("# not a heading\nline")
    assert run_extraction(str(f), "text", ctx()).markdown.startswith("\\# not a heading")


def test_scanned_pages_get_markdown_and_page_markers(sample):
    result = run_extraction(sample("scanned_letter.pdf"), "pdf", ctx())
    assert result.markdown.count("<!-- page: ") == 3
    assert {c.page_start for c in result.chunks} >= {1, 3}


def test_email_with_attachments(sample):
    path = sample("email_invoice.eml")
    assert detect_format(path, "email_invoice.eml").kind == "eml"
    result = run_extraction(path, "eml", ctx())
    md = result.markdown
    assert md.startswith("<!-- page: 1 -->\n\n# Factura INV-2026-0917 y contrato")
    assert "| From | billing@acme.example.com |" in md
    assert md_tables(md)[1] == [["Documento", "Total"], ["INV-2026-0917", "$5,800.00 MXN"]]  # HTML body table
    assert "## Attachment: invoice_digital.pdf" in md and "## Attachment: invoice_scan.png" in md
    attachments = {a["filename"]: a for a in result.metadata["email"]["attachments"]}
    assert attachments["invoice_digital.pdf"]["processed"] and attachments["invoice_scan.png"]["kind"] == "image"
    assert not attachments["tool.exe"]["processed"] and "unsupported" in attachments["tool.exe"]["skipped"]
    assert len(result.pages) == 1 + 2 + 1  # body + 2-page PDF + image


def test_chunk_options_are_applied(sample):
    small = run_extraction(sample("scanned_letter.pdf"), "pdf", ctx(chunk_size_tokens=100, chunk_overlap_tokens=10))
    large = run_extraction(sample("scanned_letter.pdf"), "pdf", ctx(chunk_size_tokens=2000, chunk_overlap_tokens=0))
    assert len(small.chunks) > len(large.chunks)
    assert small.metadata["stats"]["chunk_size_tokens"] == 100
    assert small.metadata["stats"]["tokenizer"] == "tiktoken:cl100k_base"
