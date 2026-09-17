"""Structure / LLM-readiness benchmark: how well does each converter turn documents into Markdown?

Each candidate runs in its own subprocess (a separate venv for pymupdf4llm 1.28, HTTP for our service)
and only writes its raw Markdown + timings. Scoring happens afterwards in this process, so worker code
imports nothing but the candidate library.

    python run_structure_benchmark.py                          # all local candidates + docintel if reachable
    python run_structure_benchmark.py --only docling,markitdown
    python run_structure_benchmark.py --summarize              # rebuild tables/charts from raw outputs
"""

from __future__ import annotations

import argparse
import html.parser
import json
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

THREADS = int(os.environ.get("BENCH_THREADS", "4"))
for var in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(var, str(THREADS))

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "structure"
RESULTS = ROOT / "results" / "structure"
LATEST_P4L_PYTHON = "/opt/p4l-latest/bin/python"
DOCINTEL_URL = os.environ.get("DOCINTEL_URL", "http://host.docker.internal:8000")

CANDIDATES = ["pymupdf_plain", "pymupdf4llm", "pymupdf4llm_guarded", "pymupdf4llm_latest", "markitdown", "docling",
              "docintel", "docintel_layout"]
LABELS = {
    "pymupdf_plain": "PyMuPDF get_text (baseline)",
    "pymupdf4llm": "pymupdf4llm 0.0.24",
    "pymupdf4llm_guarded": "pymupdf4llm 0.0.24 + coverage guard",
    "pymupdf4llm_latest": "pymupdf4llm 1.28 (+layout)",
    "markitdown": "MarkItDown 0.1.7",
    "docling": "Docling 2.128 (+RapidOCR)",
    "docintel": "docintel service (API)",
    "docintel_layout": "docintel service, layout pipeline (API)",
}
LICENSES = {
    "pymupdf_plain": "AGPL-3.0 / commercial",
    "pymupdf4llm": "AGPL-3.0 / commercial",
    "pymupdf4llm_guarded": "AGPL-3.0 / commercial",
    "pymupdf4llm_latest": "AGPL-3.0 / commercial",
    "markitdown": "MIT (pdfminer MIT, mammoth BSD)",
    "docling": "MIT (models: Apache-2.0 / CDLA)",
    "docintel": "this project",
    "docintel_layout": "this project (Docling MIT inside)",
}
# root distributions (with extras) whose dependency closure approximates the install footprint
FOOTPRINT_ROOTS = {
    "pymupdf_plain": [("pymupdf", set())],
    "pymupdf4llm": [("pymupdf4llm", set())],
    "pymupdf4llm_guarded": [("pymupdf4llm", set())],
    "markitdown": [("markitdown", {"pdf", "docx"})],
    "docling": [("docling", set())],
}
DOC_TYPES = ["digital_pdf", "scanned_pdf", "table_photo", "docx", "real_pdf"]


# ================================================================ workers (candidate side)


def _read_manifest() -> list[dict]:
    return json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def load_candidate(name: str):
    """Return convert(sample) -> markdown, or raise NotImplementedError for unsupported inputs."""
    if name == "pymupdf_plain":
        import fitz

        def convert(sample):
            if sample["path"].endswith(".docx"):
                raise NotImplementedError("PyMuPDF (open source) does not read DOCX")
            with fitz.open(ROOT / sample["path"]) as doc:
                return "\n\n".join(page.get_text("text", sort=True) for page in doc)
        return convert

    if name in ("pymupdf4llm", "pymupdf4llm_latest"):
        if name == "pymupdf4llm_latest":
            try:  # 1.27+: importing pymupdf.layout switches pymupdf4llm to its layout-model pipeline
                import pymupdf.layout  # noqa: F401
            except ImportError:
                pass
        import pymupdf4llm

        def convert(sample):
            if sample["path"].endswith(".docx"):
                raise NotImplementedError("pymupdf4llm does not read DOCX")
            chunks = pymupdf4llm.to_markdown(str(ROOT / sample["path"]), page_chunks=True, show_progress=False)
            return "\n\n".join(c["text"] for c in chunks)
        return convert

    if name == "pymupdf4llm_guarded":
        import re as _re

        import fitz
        import pymupdf4llm

        def words(text):
            return len(_re.findall(r"\w+", text))

        def convert(sample):
            """pymupdf4llm per page, with a guard against silently dropped text.

            pymupdf4llm skips text it considers part of vector graphics (e.g. text drawn over coloured
            boxes on brochure covers). If a page's Markdown keeps <90% of the words PyMuPDF sees on it,
            retry with ignore_graphics=True (loses line-based table detection on that page), then fall
            back to plain text.
            """
            if sample["path"].endswith(".docx"):
                raise NotImplementedError("pymupdf4llm does not read DOCX")
            path = str(ROOT / sample["path"])
            parts = []
            with fitz.open(path) as doc:
                for i, page in enumerate(doc):
                    expected = words(page.get_text("text"))
                    md = pymupdf4llm.to_markdown(doc, pages=[i], show_progress=False)
                    if expected and words(md) < 0.9 * expected:
                        md = pymupdf4llm.to_markdown(doc, pages=[i], ignore_graphics=True, show_progress=False)
                    if expected and words(md) < 0.9 * expected:
                        md = page.get_text("text", sort=True)
                    parts.append(md)
            return "\n\n".join(parts)
        return convert

    if name == "markitdown":
        from markitdown import MarkItDown

        md = MarkItDown(enable_plugins=False)

        def convert(sample):
            return md.convert(str(ROOT / sample["path"])).text_content or ""
        return convert

    if name == "docling":
        import torch
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
        try:
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        except ImportError:  # older layout of the package
            from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions

        torch.set_num_threads(THREADS)
        artifacts = os.environ.get("DOCLING_ARTIFACTS")

        def make(ocr: bool) -> DocumentConverter:
            opts = PdfPipelineOptions(artifacts_path=artifacts)
            opts.do_ocr = ocr
            opts.do_table_structure = True
            opts.table_structure_options.do_cell_matching = True
            opts.accelerator_options = AcceleratorOptions(num_threads=THREADS, device=AcceleratorDevice.CPU)
            if ocr:
                # RapidOCR, not Tesseract: with Tesseract CLI Docling returned empty table cells on scans (see README)
                opts.ocr_options = RapidOcrOptions(force_full_page_ocr=True)
            return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
                                                     InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)})

        plain, with_ocr = make(False), make(True)

        def convert(sample):
            converter = with_ocr if sample["scanned"] else plain
            return converter.convert(str(ROOT / sample["path"])).document.export_to_markdown()
        return convert

    raise ValueError(name)


def _peak_rss_mb() -> float:
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss + resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(usage / 1024, 1)  # KB on Linux


def worker(name: str) -> None:
    samples = _read_manifest()
    t0 = time.perf_counter()
    convert = load_candidate(name)
    cold_start = time.perf_counter() - t0

    # warm-up on one digital and (if relevant) one scanned sample, so timings below are warm
    warm = {}
    for s in sorted(samples, key=lambda x: x["pages"]):  # smallest documents: warm-up cost stays low
        warm.setdefault(s["scanned"], s)
    first_call = None
    for s in warm.values():
        t0 = time.perf_counter()
        try:
            convert(s)
        except Exception:
            pass
        first_call = first_call or round(time.perf_counter() - t0, 3)

    out_dir = RESULTS / "outputs" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for s in samples:
        rec = {"id": s["id"], "pages": s["pages"]}
        t0 = time.perf_counter()
        try:
            markdown = convert(s)
            rec["seconds"] = round(time.perf_counter() - t0, 3)
            (out_dir / f"{s['id']}.md").write_text(markdown, encoding="utf-8")
        except NotImplementedError as exc:
            rec["unsupported"] = str(exc)
        except Exception as exc:  # a crash on one input is a result, not a benchmark failure
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        records.append(rec)
        print(f"   {s['id']:22} {rec.get('seconds', rec.get('unsupported') or rec.get('error'))}", flush=True)
    raw = {"candidate": name, "cold_start_s": round(cold_start, 3), "first_call_s": first_call,
           "peak_rss_mb": _peak_rss_mb(), "records": records}
    (RESULTS / f"raw_{name}.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")


def worker_docintel(name: str = "docintel", extra_fields: dict | None = None) -> bool:
    """Our own service, end to end through its public API. Returns False (skipped) if not available."""
    import urllib.error
    import urllib.request
    import uuid

    def req(method, path, body=None, headers=None, timeout=60):
        r = urllib.request.Request(DOCINTEL_URL + path, data=body, method=method, headers=headers or {})
        with urllib.request.urlopen(r, timeout=timeout) as res:
            return res.status, res.read()

    try:
        status, _ = req("GET", "/health", timeout=5)
    except Exception as exc:
        print(f"   docintel unreachable at {DOCINTEL_URL}: {exc}")
        return False

    samples = _read_manifest()
    out_dir = RESULTS / "outputs" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for s in samples:
        path = ROOT / s["path"]
        boundary = uuid.uuid4().hex
        fields = {"force": "true", **(extra_fields or {})}
        body = ("".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                        for k, v in fields.items())
                + f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        rec = {"id": s["id"], "pages": s["pages"]}
        try:
            _, raw = req("POST", "/v1/documents", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
            job = json.loads(raw)
            deadline = time.time() + 3600  # layout pipeline: ~5-10 s/page, concurrency 1
            while not job["is_terminal"] and time.time() < deadline:
                time.sleep(1)
                job = json.loads(req("GET", f"/v1/jobs/{job['id']}")[1])
            if job["status"] != "SUCCEEDED":
                rec["error"] = f"job {job['status']}: {(job.get('error') or {}).get('code')}"
            else:
                try:
                    _, md = req("GET", f"/v1/jobs/{job['id']}/markdown")
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        print("   docintel has no /markdown endpoint yet: skipped")
                        return False
                    raise
                pages = job.get("page_count") or s["pages"]
                rec["seconds"] = round((job.get("duration_s") or 0), 3)
                rec["pages"] = s["pages"]
                rec["service_pages"] = pages
                (out_dir / f"{s['id']}.md").write_text(md.decode("utf-8"), encoding="utf-8")
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        records.append(rec)
        print(f"   {s['id']:22} {rec.get('seconds', rec.get('error'))}", flush=True)
    raw = {"candidate": name, "cold_start_s": None, "first_call_s": None, "peak_rss_mb": None,
           "records": records}
    (RESULTS / f"raw_{name}.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


# ================================================================ parsing & scoring (main side)

_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "•": " ", " ": " "})


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"</?(?:sup|sub|b|i|u|em|strong|span|mark|s)\b[^>]*>", "", text)  # inline HTML some tools emit
    text = re.sub(r"[*_`]+", "", text)
    text = text.replace("\\", "")
    return re.sub(r"\s+", " ", text).strip().lower()


class _HtmlTables(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")  # also compact "| - | - |"


def parse_markdown(md: str) -> dict:
    headings, items, tables, current = [], [], [], None
    in_code = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("|"):
            if SEPARATOR.match(stripped):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", stripped.strip("|"))]
            if current is None:
                current = []
                tables.append(current)
            current.append(cells)
            continue
        current = None
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", stripped)
        if m:
            headings.append((len(m.group(1)), m.group(2)))
            continue
        m = re.match(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)$", line)
        if m and m.group(1).strip():
            items.append(m.group(1))
    if "<table" in md.lower():
        parser = _HtmlTables()
        parser.feed(md)
        tables += [t for t in parser.tables if t]
    return {"headings": headings, "items": items, "tables": tables}


def strip_markdown(md: str) -> str:
    md = re.sub(r"<!--.*?-->", " ", md, flags=re.S)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"<[^>]+>", " ", md)
    lines = []
    for line in md.splitlines():
        s = line.strip()
        if SEPARATOR.match(s) and "-" in s or re.fullmatch(r"-{3,}|\*{3,}|_{3,}|```.*", s):
            continue
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", s)
        s = s.replace("|", " ")
        lines.append(s)
    return "\n".join(lines)


def _similar(a: str, b: str) -> bool:
    from rapidfuzz import fuzz

    return a == b or (len(a) > 3 and fuzz.ratio(a, b) >= 90)


def score_headings(gt: list, pred: list) -> dict | None:
    if not gt:
        return None
    used, matched, matched_any = set(), 0, 0
    gt_n = [(lvl, norm(t)) for lvl, t in gt]
    pred_n = [(lvl, norm(t)) for lvl, t in pred]
    for lvl, text in gt_n:
        hit = next((i for i, (pl, pt) in enumerate(pred_n) if i not in used and abs(pl - lvl) <= 1 and _similar(pt, text)), None)
        if hit is not None:
            used.add(hit)
            matched += 1
        if any(_similar(pt, text) for _, pt in pred_n):
            matched_any += 1
    precision = matched / len(pred_n) if pred_n else 0.0
    recall = matched / len(gt_n)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"heading_precision": precision, "heading_recall": recall, "heading_f1": f1,
            "heading_recall_any_level": matched_any / len(gt_n)}


def score_items(gt: list, pred: list) -> dict | None:
    if not gt:
        return None
    pred_n = [norm(p) for p in pred]
    hits = sum(1 for g in gt if any(_similar(p, norm(g)) for p in pred_n))
    return {"list_recall": hits / len(gt)}


def score_tables(gt_tables: list, pred_tables: list) -> dict | None:
    """Cell F1: rows are matched greedily by shared cells, then cells are counted inside matched rows.

    All tables of a document are pooled, so a table split across pages (or merged) is not punished, and
    exact duplicate predicted rows (a header repeated on every page) are counted once.
    """
    from collections import Counter

    if not gt_tables:
        return None
    gt_rows = [[norm(c) for c in row] for t in gt_tables for row in t]
    seen, pred_rows = set(), []
    for t in pred_tables:
        for row in t:
            cells = tuple(norm(c) for c in row if norm(c))
            if cells and cells not in seen:
                seen.add(cells)
                pred_rows.append(list(cells))
    gt_cells = sum(len(r) for r in gt_rows)
    pred_cells = sum(len(r) for r in pred_rows)
    pairs = []
    for gi, g in enumerate(gt_rows):
        gc = Counter(g)
        for pi, p in enumerate(pred_rows):
            overlap = sum((gc & Counter(p)).values())
            if overlap:
                pairs.append((overlap, gi, pi))
    pairs.sort(reverse=True)
    used_g, used_p, tp = set(), set(), 0
    for overlap, gi, pi in pairs:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        tp += overlap
    precision = tp / pred_cells if pred_cells else 0.0
    recall = tp / gt_cells
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"table_precision": precision, "table_recall": recall, "table_f1": f1,
            "tables_detected": len(pred_tables), "tables_expected": len(gt_tables)}


def score_text(gt_plain: str, md: str) -> dict:
    from collections import Counter

    from rapidfuzz.distance import Levenshtein

    ref, hyp = norm(gt_plain), norm(strip_markdown(md))
    cer = Levenshtein.distance(ref, hyp) / max(len(ref), 1)
    rb, hb = Counter(re.findall(r"\w+", ref)), Counter(re.findall(r"\w+", hyp))
    common = sum((rb & hb).values())
    p = common / max(sum(hb.values()), 1)
    r = common / max(sum(rb.values()), 1)
    return {"cer": min(cer, 1.0), "bow_f1": 2 * p * r / (p + r) if p + r else 0.0}


# ================================================================ footprint


def _closure(roots: list[tuple[str, set]]) -> set[str]:
    from importlib import metadata

    from packaging.requirements import Requirement

    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name, extras = stack.pop()
        key = name.lower().replace("_", "-")
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        if key in seen and not extras:
            continue
        seen.add(key)
        for spec in dist.requires or []:
            req = Requirement(spec)
            env_ok = True
            if req.marker is not None:
                env_ok = any(req.marker.evaluate({"extra": e}) for e in (extras or {""})) if "extra" in str(req.marker) \
                    else req.marker.evaluate()
            if env_ok:
                stack.append((req.name, set(req.extras)))
    return seen


def footprint_mb(name: str) -> float | None:
    from importlib import metadata

    if name.startswith("docintel"):
        return None
    if name == "pymupdf4llm_latest":
        site = next(Path("/opt/p4l-latest/lib").glob("python3*/site-packages"), None)
        if not site:
            return None
        skip = ("pip", "setuptools", "_distutils_hack")
        total = sum(p.stat().st_size for p in site.rglob("*") if p.is_file()
                    and not p.relative_to(site).parts[0].startswith(skip))
        return round(total / 2**20, 1)
    total = 0
    for dist_name in _closure(FOOTPRINT_ROOTS[name]):
        try:
            dist = metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            continue
        for f in dist.files or []:
            p = Path(dist.locate_file(f))
            if p.is_file():
                total += p.stat().st_size
    if name == "docling" and os.environ.get("DOCLING_ARTIFACTS"):
        total += sum(p.stat().st_size for p in Path(os.environ["DOCLING_ARTIFACTS"]).rglob("*") if p.is_file())
    return round(total / 2**20, 1)


# ================================================================ summary


def summarize() -> None:
    samples = {s["id"]: s for s in _read_manifest()}
    gts = {sid: json.loads((ROOT / s["gt"]).read_text(encoding="utf-8")) for sid, s in samples.items()}
    rows, engines = [], []
    for name in CANDIDATES:
        raw_path = RESULTS / f"raw_{name}.json"
        if not raw_path.exists():
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        per_sample = []
        for rec in raw["records"]:
            s, gt = samples.get(rec["id"]), gts.get(rec["id"])
            if s is None:
                continue
            row = {"candidate": name, "id": rec["id"], "doc_type": s["doc_type"], "lang": s["lang"],
                   "pages": s["pages"]}
            if "unsupported" in rec:
                row["status"] = "unsupported"
            elif "error" in rec:
                row["status"] = "error"
                row["error"] = rec["error"]
            else:
                md = (RESULTS / "outputs" / name / f"{rec['id']}.md").read_text(encoding="utf-8")
                parsed = parse_markdown(md)
                row["status"] = "ok"
                row["sec_per_page"] = rec["seconds"] / max(s["pages"], 1)
                row.update(score_text(gt["plain"], md))
                for part in (score_headings(gt["headings"], parsed["headings"]), score_items(gt["list_items"], parsed["items"]),
                             score_tables(gt["tables"], parsed["tables"])):
                    if part:
                        row.update(part)
                row["headings_found"] = len(parsed["headings"])
                row["tables_found"] = len(parsed["tables"])
                row["list_items_found"] = len(parsed["items"])
                row["preview"] = md[:400]
            per_sample.append(row)
        rows += per_sample
        ok = [r for r in per_sample if r["status"] == "ok"]

        def macro(metric, pool=ok):
            by_type = {}
            for r in pool:
                if metric in r and r["doc_type"] != "real_pdf":  # real docs have no structure ground truth
                    by_type.setdefault(r["doc_type"], []).append(r[metric])
            return statistics.mean(statistics.mean(v) for v in by_type.values()) if by_type else None

        real = [r for r in ok if r["doc_type"] == "real_pdf"]
        synthetic = [r for r in ok if r["doc_type"] != "real_pdf"]
        total_pages = sum(r["pages"] for r in synthetic)
        engines.append({
            "candidate": name, "label": LABELS[name], "license": LICENSES[name],
            "coverage": f"{len(ok)}/{len(per_sample)}",
            "errors": sum(1 for r in per_sample if r["status"] == "error"),
            "heading_f1": macro("heading_f1"), "heading_recall_any_level": macro("heading_recall_any_level"),
            "list_recall": macro("list_recall"), "table_f1": macro("table_f1"),
            "cer": macro("cer"), "bow_f1": macro("bow_f1"),
            "sec_per_page": (sum(r["sec_per_page"] * r["pages"] for r in synthetic) / total_pages) if total_pages else None,
            "real_bow_f1": statistics.mean(r["bow_f1"] for r in real) if real else None,
            "real_min_bow_f1": min(r["bow_f1"] for r in real) if real else None,
            "real_sec_per_page": (sum(r["sec_per_page"] * r["pages"] for r in real) / sum(r["pages"] for r in real))
            if real else None,
            "cold_start_s": raw.get("cold_start_s"), "peak_rss_mb": raw.get("peak_rss_mb"),
            "footprint_mb": raw.get("footprint_mb"),
        })

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "results.json").write_text(json.dumps({"samples": rows, "candidates": engines}, indent=2,
                                                     ensure_ascii=False), encoding="utf-8")
    import csv

    fields = ["candidate", "id", "doc_type", "lang", "pages", "status", "heading_precision", "heading_recall",
              "heading_f1", "heading_recall_any_level", "list_recall", "table_precision", "table_recall", "table_f1",
              "tables_detected", "tables_expected", "headings_found", "tables_found", "list_items_found", "cer", "bow_f1",
              "sec_per_page", "error"]
    with open(RESULTS / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    write_markdown(rows, engines)
    plot(rows, engines)
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


def _f(v, digits=2):
    return "–" if v is None or v == "" else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def write_markdown(rows: list[dict], engines: list[dict]) -> None:
    def table(header, body):
        return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                         + ["| " + " | ".join(str(c) for c in r) + " |" for r in body])

    out = ["# Structure benchmark summary", "",
           f"Threads per candidate: {THREADS}. Heading F1 matches heading text with level tolerance ±1. Table F1 is "
           "cell-level (rows matched greedily, all tables of a document pooled). CER is computed after stripping "
           "Markdown syntax. Macro = mean over the synthetic document types (which have ground truth) where the "
           "candidate produced output. Scanned inputs and photos need OCR; candidates without OCR score 0 there. "
           "Real documents (public-domain IRS PDFs) have no structure ground truth: they are scored only by word "
           "recall against the publisher's text layer (BoW F1), to catch silently dropped text.", "",
           "## Overall", "",
           table(["Candidate", "Coverage", "Heading F1", "Heading recall (any level)", "List recall", "Table cell F1",
                  "CER", "BoW F1", "s/page", "Real docs BoW F1 (min)", "Real s/page", "Cold start s", "Peak RSS MB",
                  "Install MB", "License"],
                 [[e["label"], e["coverage"], _f(e["heading_f1"]), _f(e["heading_recall_any_level"]),
                   _f(e["list_recall"]), _f(e["table_f1"]), _f(e["cer"], 3), _f(e["bow_f1"]),
                   _f(e["sec_per_page"], 3),
                   f"{_f(e['real_bow_f1'])} ({_f(e['real_min_bow_f1'])})" if e.get("real_bow_f1") is not None else "–",
                   _f(e.get("real_sec_per_page"), 3), _f(e["cold_start_s"]), _f(e["peak_rss_mb"], 0),
                   _f(e["footprint_mb"], 0), e["license"]] for e in engines]), ""]
    types = [t for t in DOC_TYPES if any(r["doc_type"] == t for r in rows)]
    for metric, title, digits in (("table_f1", "Table cell F1 by document type", 2),
                                  ("heading_f1", "Heading F1 by document type", 2),
                                  ("list_recall", "List-item recall by document type", 2),
                                  ("cer", "CER (Markdown stripped) by document type", 3),
                                  ("sec_per_page", "Seconds per page by document type", 3)):
        body = []
        for t in types:
            cells = [t]
            for e in engines:
                vals = [r[metric] for r in rows if r["candidate"] == e["candidate"] and r["doc_type"] == t
                        and r["status"] == "ok" and metric in r]
                status = {r["status"] for r in rows if r["candidate"] == e["candidate"] and r["doc_type"] == t}
                cells.append(_f(statistics.mean(vals), digits) if vals else ("unsupported" if status == {"unsupported"}
                                                                            else "error" if "error" in status else "–"))
            body.append(cells)
        out += [f"## {title}", "", table(["Document type"] + [e["label"] for e in engines], body), ""]
    out += ["## Per sample", "",
            table(["Candidate", "Sample", "Status", "Heading F1", "List recall", "Table F1", "Tables found/expected",
                   "Headings found", "CER", "BoW F1", "s/page"],
                  [[LABELS[r["candidate"]], r["id"], r["status"], _f(r.get("heading_f1")), _f(r.get("list_recall")),
                    _f(r.get("table_f1")),
                    f"{r['tables_found']}/{r['tables_expected']}" if "tables_expected" in r
                    else (str(r["tables_found"]) if "tables_found" in r else "–"),
                    _f(r.get("headings_found")), _f(r.get("cer"), 3), _f(r.get("bow_f1")), _f(r.get("sec_per_page"), 3)]
                   for r in rows]), ""]
    errors = [r for r in rows if r["status"] == "error"]
    if errors:
        out += ["## Errors", ""] + [f"- {LABELS[r['candidate']]} on `{r['id']}`: {r['error']}" for r in errors] + [""]
    (RESULTS / "summary.md").write_text("\n".join(out), encoding="utf-8")


SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = {"pymupdf_plain": "#2a78d6", "pymupdf4llm": "#eb6834", "pymupdf4llm_guarded": "#008300",
          "pymupdf4llm_latest": "#1baf7a", "markitdown": "#eda100", "docling": "#e87ba4", "docintel": "#4a3aa7",
          "docintel_layout": "#e34948"}
MARKERS = {"pymupdf_plain": "o", "pymupdf4llm": "s", "pymupdf4llm_guarded": "v", "pymupdf4llm_latest": "^",
           "markitdown": "D", "docling": "P", "docintel": "X", "docintel_layout": "*"}


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot(rows: list[dict], engines: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                         "text.color": INK, "axes.labelcolor": INK2})

    # 1) Small multiples: table cell F1 and heading F1 per document type, one marker per candidate
    types = [t for t in DOC_TYPES if t != "real_pdf" and any(r["doc_type"] == t for r in rows)]
    metrics = (("table_f1", "Table cell F1 (higher is better)"), ("heading_f1", "Heading F1 (higher is better)"))
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.55 * len(types) + 2.2), dpi=200, sharey=True)
    offsets = {e["candidate"]: (i - (len(engines) - 1) / 2) * 0.12 for i, e in enumerate(engines)}
    for ax, (metric, xlabel) in zip(axes, metrics):
        _style(ax)
        for k in range(len(types) - 1):
            ax.axhline(k + 0.5, color=GRID, linewidth=0.6, zorder=1)
        for e in engines:
            xs, ys = [], []
            for k, t in enumerate(types):
                vals = [r[metric] for r in rows if r["candidate"] == e["candidate"] and r["doc_type"] == t
                        and r["status"] == "ok" and metric in r]
                if vals:
                    xs.append(statistics.mean(vals))
                    ys.append(len(types) - 1 - k + offsets[e["candidate"]])
            ax.scatter(xs, ys, s=42, color=SERIES[e["candidate"]], marker=MARKERS[e["candidate"]], edgecolors=SURFACE,
                       linewidths=1.3, zorder=3, label=e["label"])
        ax.set_xlim(-0.03, 1.03)
        ax.set_xlabel(xlabel)
    axes[0].set_yticks(range(len(types)))
    axes[0].set_yticklabels(list(reversed(types)))
    fig.text(0.015, 0.985, "Structure preserved in Markdown, by document type", ha="left", va="top", fontsize=12)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.945), ncol=3, frameon=False, fontsize=8.5,
               handletextpad=0.3, columnspacing=1.2)
    fig.tight_layout(rect=(0, 0, 1, 0.83))
    fig.savefig(RESULTS / "structure_by_doc_type.png")
    plt.close(fig)

    # 2) Structure score vs speed
    scored = [e for e in engines if e["sec_per_page"]]
    fig, ax = plt.subplots(figsize=(7.5, 4.8), dpi=200)
    _style(ax)
    ax.grid(axis="both", color=GRID, linewidth=0.8)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(AXIS)
    for e in scored:
        parts = [v for v in (e["heading_f1"], e["table_f1"], e["list_recall"]) if v is not None]
        e["structure_score"] = statistics.mean(parts) if parts else 0.0
        ax.scatter(e["sec_per_page"], e["structure_score"], s=80, color=SERIES[e["candidate"]],
                   marker=MARKERS[e["candidate"]], edgecolors=SURFACE, linewidths=2, zorder=3, label=e["label"])
        offset = {"pymupdf4llm_guarded": (8, -26), "pymupdf4llm": (-10, -26), "markitdown": (-10, 10)}.get(e["candidate"], (8, 6))
        ax.annotate(f"{e['label']}\n{e['sec_per_page']:.3f} s/page · score {e['structure_score']:.2f}",
                    (e["sec_per_page"], e["structure_score"]), textcoords="offset points", xytext=offset,
                    fontsize=7.5, color=INK2, ha="right" if offset[0] < 0 else "left")
    ax.set_xscale("log")
    ax.set_xlabel(f"Mean seconds per page (log scale, {THREADS} threads)")
    ax.set_ylabel("Structure score (mean of heading F1, table F1, list recall)")
    ax.set_ylim(0, 1.1)
    xs = [e["sec_per_page"] for e in scored]
    ax.set_xlim(min(xs) / 3, max(xs) * 12)
    fig.text(0.015, 0.985, "Markdown structure vs speed (upper-left is better)", ha="left", va="top", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(RESULTS / "structure_vs_speed.png")
    plt.close(fig)


# ================================================================ orchestration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--only")
    parser.add_argument("--summarize", action="store_true")
    parser.add_argument("--footprint")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    if args.footprint:
        print(json.dumps(footprint_mb(args.footprint)))
        return
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not args.summarize:
        names = args.only.split(",") if args.only else CANDIDATES
        for name in names:
            t0 = time.perf_counter()
            print(f"== {name}", flush=True)
            if name.startswith("docintel"):
                extra = {"pipeline": "layout"} if name == "docintel_layout" else None
                if not worker_docintel(name, extra):
                    (RESULTS / f"raw_{name}.json").unlink(missing_ok=True)
                    print(f"   {name} skipped")
                continue
            python = LATEST_P4L_PYTHON if name == "pymupdf4llm_latest" else sys.executable
            if name == "pymupdf4llm_latest" and not Path(python).exists():
                print("   venv for pymupdf4llm 1.28 not found: skipped")
                continue
            proc = subprocess.run([python, __file__, "--worker", name])
            if proc.returncode != 0:
                print(f"   {name} worker crashed (exit {proc.returncode})")
                continue
            raw_path = RESULTS / f"raw_{name}.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["footprint_mb"] = footprint_mb(name)
            raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"   {name} finished in {time.perf_counter() - t0:.0f}s", flush=True)
    summarize()


if __name__ == "__main__":
    main()
