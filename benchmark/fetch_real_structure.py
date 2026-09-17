"""Download a few real, public-domain documents for the structure benchmark (optional, needs network).

US federal government works (here: IRS publications and forms) are in the public domain (17 U.S.C. § 105).
There is no hand-made Markdown ground truth for them, so they are used as a *real-document smoke test*:
the reference is the publisher's own text layer (for word recall and CER), and headings/tables found are
reported as counts, not scored as F1.

    python fetch_real_structure.py   # writes data/real_structure/*.pdf + *.gt.json
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import fitz

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "real_structure"

SOURCES = [
    # (name, url, pages to keep (0-based, inclusive range) or None for all)
    ("irs_fw9", "https://www.irs.gov/pub/irs-pdf/fw9.pdf", (0, 5)),
    ("irs_p15t_excerpt", "https://www.irs.gov/pub/irs-pdf/p15t.pdf", (0, 5)),
    ("irs_p1", "https://www.irs.gov/pub/irs-pdf/p1.pdf", None),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, url, pages in SOURCES:
        dst = OUT / f"{name}.pdf"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (benchmark)"})
            data = urllib.request.urlopen(request, timeout=60).read()
        except Exception as exc:
            print(f"skip {name}: {exc}")
            continue
        src = fitz.open(stream=data, filetype="pdf")
        doc = fitz.open()
        first, last = pages if pages else (0, src.page_count - 1)
        doc.insert_pdf(src, from_page=first, to_page=min(last, src.page_count - 1))
        doc.save(dst)
        plain = "\n".join(page.get_text("text", sort=True) for page in doc)
        gt = {"markdown": None, "plain": plain, "headings": [], "list_items": [], "tables": [],
              "reference": "publisher text layer (PyMuPDF get_text sort=True)", "source": url,
              "license": "US federal government work, public domain (17 U.S.C. 105)"}
        dst.with_suffix(".gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest.append({"name": name, "url": url, "pages": doc.page_count})
        print(f"{name}: {doc.page_count} pages")
    (OUT / "SOURCES.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
