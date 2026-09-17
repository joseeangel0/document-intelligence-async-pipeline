"""Benchmark PDF text-layer extractors and OCR engines against the generated dataset.

Every candidate runs in its own subprocess so that import time, model load time and
peak memory are measured in isolation. Usage:

    python run_benchmark.py                 # run everything, then summarize + plot
    python run_benchmark.py --only tesseract,pymupdf
    python run_benchmark.py --summarize     # rebuild summary/charts from results/raw_*.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

THREADS = int(os.environ.get("BENCH_THREADS", "4"))
for var in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(var, str(THREADS))

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "generated"
RESULTS = ROOT / "results"

OCR_ENGINES = ["tesseract", "tesseract_prep", "rapidocr_v6", "rapidocr_v5_latin", "easyocr"]
PDF_EXTRACTORS = ["pymupdf", "pymupdf_sorted", "pypdfium2", "pdfplumber", "pypdf", "pdfminer"]

LABELS = {
    "tesseract": "Tesseract 5.3",
    "tesseract_prep": "Tesseract 5.3 + OpenCV prep",
    "rapidocr_v6": "RapidOCR PP-OCRv6 small",
    "rapidocr_v5_latin": "RapidOCR PP-OCRv5 latin",
    "easyocr": "EasyOCR 1.7",
    "pymupdf": "PyMuPDF",
    "pymupdf_sorted": "PyMuPDF (sort=True)",
    "pypdfium2": "pypdfium2",
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "pdfminer": "pdfminer.six",
}

LICENSES = {
    "tesseract": "Apache-2.0",
    "tesseract_prep": "Apache-2.0 (+ OpenCV Apache-2.0)",
    "rapidocr_v6": "Apache-2.0 (models Apache-2.0)",
    "rapidocr_v5_latin": "Apache-2.0 (models Apache-2.0)",
    "easyocr": "Apache-2.0",
    "pymupdf": "AGPL-3.0 or commercial",
    "pymupdf_sorted": "AGPL-3.0 or commercial",
    "pypdfium2": "Apache-2.0 / BSD-3 (PDFium)",
    "pdfplumber": "MIT",
    "pypdf": "BSD-3-Clause",
    "pdfminer": "MIT",
}

# Python distributions (and extra dirs) each candidate pulls into an image.
FOOTPRINT = {
    "tesseract": {"dists": ["pytesseract"], "apt": ["tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-spa",
                                                   "libtesseract5", "liblept5"]},
    "tesseract_prep": {"dists": ["pytesseract", "opencv-python-headless"],
                       "apt": ["tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-spa", "libtesseract5", "liblept5"]},
    "rapidocr_v6": {"dists": ["rapidocr", "onnxruntime", "opencv-python", "pyclipper", "shapely", "omegaconf"]},
    "rapidocr_v5_latin": {"dists": ["rapidocr", "onnxruntime", "opencv-python", "pyclipper", "shapely", "omegaconf"]},
    "easyocr": {"dists": ["easyocr", "torch", "torchvision", "opencv-python-headless", "scikit-image", "scipy",
                          "pyclipper", "shapely"], "dirs": ["~/.EasyOCR"]},
    "pymupdf": {"dists": ["pymupdf"]},
    "pymupdf_sorted": {"dists": ["pymupdf"]},
    "pypdfium2": {"dists": ["pypdfium2"]},
    "pdfplumber": {"dists": ["pdfplumber", "pdfminer.six", "pypdfium2"]},
    "pypdf": {"dists": ["pypdf"]},
    "pdfminer": {"dists": ["pdfminer.six"]},
}


# ---------------------------------------------------------------- metrics

_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "•": " ", " ": " "})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", text).strip()


def bag(text: str) -> Counter:
    return Counter(re.findall(r"\w+", normalize(text).lower()))


def score(reference: str, hypothesis: str) -> dict:
    from rapidfuzz.distance import Levenshtein

    ref, hyp = normalize(reference), normalize(hypothesis)
    ref_tokens, hyp_tokens = ref.split(), hyp.split()
    cer = Levenshtein.distance(ref, hyp) / max(len(ref), 1)
    wer = Levenshtein.distance(ref_tokens, hyp_tokens) / max(len(ref_tokens), 1)
    rb, hb = bag(reference), bag(hypothesis)
    overlap = sum((rb & hb).values())
    precision = overlap / max(sum(hb.values()), 1)
    recall = overlap / max(sum(rb.values()), 1)
    f1 = 0.0 if overlap == 0 else 2 * precision * recall / (precision + recall)
    return {"cer": round(cer, 4), "wer": round(wer, 4), "bow_f1": round(f1, 4),
            "ref_chars": len(ref), "hyp_chars": len(hyp)}


def rss_mb() -> float:
    import psutil
    return psutil.Process().memory_info().rss / 2**20


def peak_rss_mb() -> tuple[float, float]:
    import resource
    self_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB on Linux
    child_peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    return self_peak, child_peak


# ---------------------------------------------------------------- OCR engines

def group_lines(boxes: list[dict]) -> str:
    """Join boxes into lines: top-to-bottom, boxes on the same line left-to-right."""
    if not boxes:
        return ""
    boxes = sorted(boxes, key=lambda b: (b["y0"] + b["y1"]) / 2)
    median_h = statistics.median(b["y1"] - b["y0"] for b in boxes) or 1
    lines: list[list[dict]] = []
    for b in boxes:
        cy = (b["y0"] + b["y1"]) / 2
        if lines and abs(cy - statistics.mean((x["y0"] + x["y1"]) / 2 for x in lines[-1])) < 0.5 * median_h:
            lines[-1].append(b)
        else:
            lines.append([b])
    return "\n".join(" ".join(b["text"] for b in sorted(line, key=lambda b: b["x0"])) for line in lines)


def _largest_gap(intervals: list[tuple[float, float]]) -> tuple[float, float]:
    """Largest empty gap in the 1-D projection of intervals -> (gap size, cut position)."""
    intervals = sorted(intervals)
    best, cut, reach = 0.0, 0.0, intervals[0][1]
    for lo, hi in intervals[1:]:
        if lo - reach > best:
            best, cut = lo - reach, (lo + reach) / 2
        reach = max(reach, hi)
    return best, cut


def _xy_cut(boxes: list[dict], median_h: float) -> list[list[dict]]:
    """Recursive XY-cut: split on the widest whitespace gap (columns need a gap >= 1 line height)."""
    if len(boxes) <= 1:
        return [boxes]
    gap_y, cut_y = _largest_gap([(b["y0"], b["y1"]) for b in boxes])
    gap_x, cut_x = _largest_gap([(b["x0"], b["x1"]) for b in boxes])
    if gap_x >= median_h and gap_x > gap_y:
        left = [b for b in boxes if (b["x0"] + b["x1"]) / 2 < cut_x]
        right = [b for b in boxes if (b["x0"] + b["x1"]) / 2 >= cut_x]
        return _xy_cut(left, median_h) + _xy_cut(right, median_h)
    if gap_y > 0:
        top = [b for b in boxes if (b["y0"] + b["y1"]) / 2 < cut_y]
        bottom = [b for b in boxes if (b["y0"] + b["y1"]) / 2 >= cut_y]
        return _xy_cut(top, median_h) + _xy_cut(bottom, median_h)
    return [boxes]


def reading_order(items: list[tuple[list, str]]) -> str:
    """Order detector boxes (4 corner points + text) the way a person reads the page.

    1. Deskew: rotate box corners by the median text-line angle so tilted scans form level lines.
    2. XY-cut: recursively split on whitespace so columns are read one after another.
    3. Inside each block, group boxes into lines.
    Tesseract does its own page segmentation (psm 3), so this is only applied to RapidOCR and EasyOCR.
    """
    import math

    if not items:
        return ""
    angles = []
    for box, _ in items:
        (x0, y0), (x1, y1) = box[0], box[1]
        if x1 - x0 > 2 * abs(y1 - y0):
            angles.append(math.atan2(y1 - y0, x1 - x0))
    theta = -statistics.median(angles) if angles else 0.0
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    boxes = []
    for box, text in items:
        pts = [(x * cos_t - y * sin_t, x * sin_t + y * cos_t) for x, y in box]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        boxes.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys), "text": text})
    median_h = statistics.median(b["y1"] - b["y0"] for b in boxes) or 1
    return "\n".join(group_lines(block) for block in _xy_cut(boxes, median_h) if block)


def preprocess_for_tesseract(img):
    """Illumination correction + light denoise + global Otsu binarization (OpenCV)."""
    import cv2
    import numpy as np
    from PIL import Image

    gray = np.asarray(img.convert("L"))
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 31)
    flat = cv2.divide(gray, background, scale=255)
    flat = cv2.medianBlur(flat, 3)
    _, binary = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(binary)


def load_ocr(name: str):
    """Import + initialize an engine; returns a callable PIL.Image -> str."""
    if name in ("tesseract", "tesseract_prep"):
        import pytesseract
        prep = name == "tesseract_prep"
        if prep:
            import cv2  # noqa: F401  (count OpenCV import in cold start)

        def run(img):
            if prep:
                img = preprocess_for_tesseract(img)
            return pytesseract.image_to_string(img, lang="eng+spa", config="--oem 1 --psm 3")
        pytesseract.get_tesseract_version()
        return run

    if name.startswith("rapidocr"):
        import numpy as np
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        # use_cls=False: the 0/180° line classifier flips some upright lines and garbles them (see README)
        params = {"Global.log_level": "error", "Global.use_cls": False,
                  "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
                  "EngineConfig.onnxruntime.inter_op_num_threads": 1}
        if name == "rapidocr_v5_latin":
            params.update({"Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                           "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
                           "Rec.lang_type": LangRec.LATIN})
        engine = RapidOCR(params=params)

        def run(img):
            out = engine(np.asarray(img.convert("RGB")))
            if out.txts is None:
                return ""
            return reading_order([(box.tolist(), txt) for box, txt in zip(out.boxes, out.txts)])
        return run

    if name == "easyocr":
        import easyocr
        import numpy as np
        import torch

        torch.set_num_threads(THREADS)
        # quantize=False: the dynamically-quantized LSTM crashes with SIGILL on torch 2.8 arm64 under OrbStack
        reader = easyocr.Reader(["en", "es"], gpu=False, verbose=False, quantize=False)

        def run(img):
            result = reader.readtext(np.asarray(img.convert("RGB")), detail=1, paragraph=False)
            return reading_order([([list(map(float, p)) for p in box], text) for box, text, _conf in result])
        return run

    raise ValueError(name)


def ocr_inputs(sample: dict):
    """Yield PIL images (one per page) for an OCR sample."""
    from PIL import Image

    path = ROOT / sample["path"]
    if sample["kind"] == "image":
        yield Image.open(path).copy()
        return
    import fitz  # scanned PDFs: rasterize at 300 dpi, as the production worker would
    with fitz.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=300, colorspace=fitz.csGRAY)
            yield Image.frombytes("L", (pix.width, pix.height), pix.samples)


def bench_ocr(name: str, samples: list[dict]) -> dict:
    samples = [s for s in samples if s["kind"] == "image" or s["category"] == "scanned_pdf"]
    rss_before = rss_mb()
    t0 = time.perf_counter()
    run = load_ocr(name)
    cold_start = time.perf_counter() - t0
    rss_loaded = rss_mb()

    warm = next(ocr_inputs(samples[0]))
    t0 = time.perf_counter()
    run(warm)
    first_call = time.perf_counter() - t0

    records = []
    for s in samples:
        texts, page_times = [], []
        for img in ocr_inputs(s):
            t0 = time.perf_counter()
            texts.append(run(img))
            page_times.append(time.perf_counter() - t0)
        hyp = "\n".join(texts)
        ref = (ROOT / s["gt"]).read_text(encoding="utf-8")
        rec = {"engine": name, "sample": s["id"], "category": s["category"], "pages": len(page_times),
               "seconds": round(sum(page_times), 4), "page_seconds": [round(t, 4) for t in page_times],
               **score(ref, hyp), "output_preview": hyp[:300]}
        records.append(rec)
        print(f"  {name:18s} {s['id']:22s} cer={rec['cer']:.3f} wer={rec['wer']:.3f} "
              f"t={rec['seconds']:.2f}s", flush=True)
    self_peak, child_peak = peak_rss_mb()
    return {"engine": name, "type": "ocr", "threads": THREADS, "cold_start_s": round(cold_start, 3),
            "first_call_s": round(first_call, 3), "rss_load_delta_mb": round(rss_loaded - rss_before, 1),
            "peak_rss_mb": round(self_peak + child_peak, 1), "records": records}


# ---------------------------------------------------------------- PDF extractors

def load_pdf(name: str):
    if name in ("pymupdf", "pymupdf_sorted"):
        import fitz
        sort = name == "pymupdf_sorted"

        def run(path):
            with fitz.open(path) as doc:
                return "\n".join(page.get_text("text", sort=sort) for page in doc)
        return run
    if name == "pypdfium2":
        import pypdfium2 as pdfium

        def run(path):
            pdf = pdfium.PdfDocument(path)
            try:
                return "\n".join(page.get_textpage().get_text_range() for page in pdf)
            finally:
                pdf.close()
        return run
    if name == "pdfplumber":
        import pdfplumber

        def run(path):
            with pdfplumber.open(path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        return run
    if name == "pypdf":
        from pypdf import PdfReader

        def run(path):
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        return run
    if name == "pdfminer":
        from pdfminer.high_level import extract_text

        def run(path):
            return extract_text(path)
        return run
    raise ValueError(name)


def bench_pdf(name: str, samples: list[dict], repeats: int = 3) -> dict:
    samples = [s for s in samples if s["kind"] == "pdf"]
    real = ROOT / "data" / "real" / "U1T02.pdf"
    rss_before = rss_mb()
    t0 = time.perf_counter()
    run = load_pdf(name)
    cold_start = time.perf_counter() - t0
    run(str(ROOT / samples[0]["path"]))  # warm-up
    records = []
    targets = [(s, ROOT / s["path"]) for s in samples]
    if real.exists():
        targets.append(({"id": "real_u1t02", "category": "real_pdf", "gt": None, "pages": 2}, real))
    for s, path in targets:
        times, hyp = [], ""
        for _ in range(repeats):
            t0 = time.perf_counter()
            hyp = run(str(path))
            times.append(time.perf_counter() - t0)
        seconds = statistics.median(times)
        rec = {"engine": name, "sample": s["id"], "category": s["category"], "pages": s.get("pages", 1),
               "seconds": round(seconds, 5), "page_seconds": [round(seconds / s.get("pages", 1), 5)],
               "output_preview": hyp[:300]}
        if s["gt"] and s["category"] != "scanned_pdf":
            rec.update(score((ROOT / s["gt"]).read_text(encoding="utf-8"), hyp))
        else:
            rec.update({"hyp_chars": len(normalize(hyp))})
        records.append(rec)
        print(f"  {name:12s} {s['id']:22s} cer={rec.get('cer', float('nan')):.3f} "
              f"chars={rec['hyp_chars']} t={seconds * 1000:.1f}ms", flush=True)
    self_peak, _ = peak_rss_mb()
    return {"engine": name, "type": "pdf", "threads": THREADS, "cold_start_s": round(cold_start, 3),
            "rss_load_delta_mb": round(rss_mb() - rss_before, 1), "peak_rss_mb": round(self_peak, 1),
            "records": records}


# ---------------------------------------------------------------- footprint

def footprint_mb(name: str) -> float:
    from importlib import metadata

    spec = FOOTPRINT[name]
    total = 0
    for dist_name in spec.get("dists", []):
        try:
            dist = metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            continue
        for f in dist.files or []:
            p = Path(dist.locate_file(f))
            if p.is_file():
                total += p.stat().st_size
    for d in spec.get("dirs", []):
        for p in Path(d).expanduser().rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    # rapidocr downloads models into its package dir; they are counted with the dist only if listed
    if name.startswith("rapidocr"):
        import rapidocr
        models = Path(rapidocr.__file__).parent / "models"
        wanted = ("PP-OCRv6", "ch_ppocr_mobile") if name == "rapidocr_v6" else ("PP-OCRv5", "ch_ppocr_mobile")
        total += sum(p.stat().st_size for p in models.glob("*.onnx") if p.name.startswith(wanted)
                     or any(w in p.name for w in wanted))
    if spec.get("apt"):
        out = subprocess.run(["dpkg-query", "-W", "-f=${Installed-Size}\n", *spec["apt"]],
                             capture_output=True, text=True).stdout
        total += sum(int(x) for x in out.split() if x.isdigit()) * 1024
    return round(total / 2**20, 1)


# ---------------------------------------------------------------- summary & charts

def pct(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return float("nan")
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def summarize() -> None:
    raws = [json.loads(p.read_text()) for p in sorted(RESULTS.glob("raw_*.json"))]
    order = {n: i for i, n in enumerate(OCR_ENGINES + PDF_EXTRACTORS)}
    raws.sort(key=lambda r: order.get(r["engine"], 99))
    (RESULTS / "results.json").write_text(json.dumps(raws, indent=2, ensure_ascii=False), encoding="utf-8")

    rows = []  # long format for CSV: engine, category, metrics
    engine_rows = []
    for raw in raws:
        by_cat = defaultdict(list)
        for r in raw["records"]:
            by_cat[r["category"]].append(r)
        for cat, recs in by_cat.items():
            scored = [r for r in recs if "cer" in r]
            page_times = [t for r in recs for t in r["page_seconds"]]
            rows.append({
                "type": raw["type"], "engine": raw["engine"], "category": cat, "samples": len(recs),
                "pages": sum(r["pages"] for r in recs),
                "cer": round(statistics.mean(r["cer"] for r in scored), 4) if scored else "",
                "wer": round(statistics.mean(r["wer"] for r in scored), 4) if scored else "",
                "bow_f1": round(statistics.mean(r["bow_f1"] for r in scored), 4) if scored else "",
                "sec_per_page_mean": round(statistics.mean(page_times), 5),
                "chars_extracted": sum(r["hyp_chars"] for r in recs),
            })
        scored = [r for r in raw["records"] if "cer" in r and not r["category"].startswith("real")]
        all_pages = [t for r in raw["records"] for t in r["page_seconds"]]
        cats = [c for c in by_cat if any("cer" in r for r in by_cat[c]) and not c.startswith("real")]
        engine_rows.append({
            "type": raw["type"], "engine": raw["engine"], "label": LABELS[raw["engine"]],
            "license": LICENSES[raw["engine"]], "footprint_mb": raw.get("footprint_mb", ""),
            "cold_start_s": raw["cold_start_s"], "first_call_s": raw.get("first_call_s", ""),
            "rss_load_delta_mb": raw["rss_load_delta_mb"], "peak_rss_mb": raw["peak_rss_mb"],
            "macro_cer": round(statistics.mean(statistics.mean(r["cer"] for r in by_cat[c] if "cer" in r)
                                               for c in cats), 4) if cats else "",
            "macro_wer": round(statistics.mean(statistics.mean(r["wer"] for r in by_cat[c] if "cer" in r)
                                               for c in cats), 4) if cats else "",
            "macro_bow_f1": round(statistics.mean(statistics.mean(r["bow_f1"] for r in by_cat[c] if "cer" in r)
                                                  for c in cats), 4) if cats else "",
            "sec_per_page_mean": round(statistics.mean(all_pages), 5),
            "sec_per_page_p95": round(pct(all_pages, 0.95), 5),
            "total_pages": len(all_pages), "scored_samples": len(scored),
        })

    with open(RESULTS / "summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with open(RESULTS / "engines.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(engine_rows[0].keys()))
        writer.writeheader()
        writer.writerows(engine_rows)

    write_markdown(rows, engine_rows)
    plot(rows, engine_rows)


def write_markdown(rows: list[dict], engine_rows: list[dict]) -> None:
    def table(header: list[str], body: list[list]) -> str:
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
        lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in body]
        return "\n".join(lines)

    def f(v, digits=3):
        return f"{v:.{digits}f}" if isinstance(v, (int, float)) and v != "" else "–"

    out = ["# Benchmark summary", "",
           f"Threads per engine: {THREADS}. "
           "CER/WER: lower is better. BoW F1 (reading-order-insensitive): higher is better. "
           "Macro averages are the mean over synthetic categories (real-document rows excluded).", ""]

    ocr_engines = [e for e in engine_rows if e["type"] == "ocr"]
    pdf_engines = [e for e in engine_rows if e["type"] == "pdf"]

    out += ["## OCR engines — overall", "", table(
        ["Engine", "Macro CER", "Macro WER", "Macro BoW F1", "s/page mean", "s/page p95", "Cold start s",
         "Load RSS MB", "Peak RSS MB", "Install MB", "License"],
        [[e["label"], f(e["macro_cer"]), f(e["macro_wer"]), f(e["macro_bow_f1"]), f(e["sec_per_page_mean"], 2),
          f(e["sec_per_page_p95"], 2), f(e["cold_start_s"], 2), f(e["rss_load_delta_mb"], 0),
          f(e["peak_rss_mb"], 0), f(e["footprint_mb"], 0), e["license"]] for e in ocr_engines]), ""]

    categories = [r["category"] for r in rows if r["type"] == "ocr"]
    categories = list(dict.fromkeys(categories))
    lookup = {(r["engine"], r["category"]): r for r in rows}
    for metric, title in (("cer", "CER"), ("wer", "WER"), ("bow_f1", "BoW F1"), ("sec_per_page_mean", "seconds per page")):
        out += [f"## OCR — {title} by category", "", table(
            ["Category"] + [e["label"] for e in ocr_engines],
            [[c] + [f(lookup[(e['engine'], c)][metric], 2 if metric == "sec_per_page_mean" else 3)
                    if (e["engine"], c) in lookup else "–" for e in ocr_engines] for c in categories]), ""]

    out += ["## PDF text-layer extractors — overall", "", table(
        ["Extractor", "Macro CER", "Macro WER", "Macro BoW F1", "ms/page mean", "ms/page p95", "Import s",
         "Install MB", "License"],
        [[e["label"], f(e["macro_cer"]), f(e["macro_wer"]), f(e["macro_bow_f1"]), f(e["sec_per_page_mean"] * 1000, 1),
          f(e["sec_per_page_p95"] * 1000, 1), f(e["cold_start_s"], 2), f(e["footprint_mb"], 1), e["license"]]
         for e in pdf_engines]), ""]
    pdf_cats = list(dict.fromkeys(r["category"] for r in rows if r["type"] == "pdf"))
    out += ["## PDF extractors — by category (CER / ms per page)", "",
            "`scanned_pdf` has no text layer: the column shows characters returned (0 = correctly signals OCR is needed).", "",
            table(["Category"] + [e["label"] for e in pdf_engines],
                  [[c] + [(f"{lookup[(e['engine'], c)]['chars_extracted']} chars"
                           if c == "scanned_pdf" else
                           (f"{f(lookup[(e['engine'], c)]['cer'])} / " if lookup[(e['engine'], c)]['cer'] != "" else "")
                           + f"{lookup[(e['engine'], c)]['sec_per_page_mean'] * 1000:.1f} ms")
                          for e in pdf_engines] for c in pdf_cats]), ""]
    (RESULTS / "summary.md").write_text("\n".join(out), encoding="utf-8")


# Palette from the dataviz reference instance (light mode); first slots validated all-pairs.
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
MARKERS = ["o", "s", "^", "D", "P"]  # secondary encoding so identity never relies on color alone


def style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot(rows: list[dict], engine_rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                         "text.color": INK, "axes.labelcolor": INK2})
    ocr = [e for e in engine_rows if e["type"] == "ocr"]
    pdf = [e for e in engine_rows if e["type"] == "pdf"]
    lookup = {(r["engine"], r["category"]): r for r in rows}

    # 1) OCR CER by category: dot plot, one row per category, one marker per engine
    cats = list(dict.fromkeys(r["category"] for r in rows if r["type"] == "ocr"))
    fig, ax = plt.subplots(figsize=(8.5, 0.5 * len(cats) + 1.8), dpi=200)
    style_axes(ax)
    offsets = [(i - (len(ocr) - 1) / 2) * 0.15 for i in range(len(ocr))]
    for k in range(len(cats) - 1):  # hairline separators between category rows
        ax.axhline(k + 0.5, color=GRID, linewidth=0.6, zorder=1)
    for i, e in enumerate(ocr):
        xs = [min(lookup[(e["engine"], c)]["cer"], 1.0) for c in cats]
        ys = [len(cats) - 1 - k + offsets[i] for k in range(len(cats))]
        ax.scatter(xs, ys, s=40, color=SERIES[i], marker=MARKERS[i], edgecolors=SURFACE, linewidths=1.5,
                   label=e["label"], zorder=3)
    ax.set_yticks(range(len(cats)))
    ax.set_yticklabels(list(reversed(cats)))
    ax.set_xlim(0, None)
    ax.set_xlabel("Character error rate (lower is better, capped at 1.0)")
    fig.text(0.015, 0.985, "OCR character error rate by input condition", ha="left", va="top", fontsize=12, color=INK)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.955), ncol=3, frameon=False,
               fontsize=8.5, handletextpad=0.3, columnspacing=1.2)
    fig.tight_layout(rect=(0, 0, 1, 0.89))
    fig.savefig(RESULTS / "ocr_cer_by_category.png")
    plt.close(fig)

    # 2) Speed vs accuracy (one point per OCR engine, direct labels)
    fig, ax = plt.subplots(figsize=(7.5, 4.8), dpi=200)
    style_axes(ax)
    ax.grid(axis="both", color=GRID, linewidth=0.8)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(AXIS)
    label_offsets = {"rapidocr_v6": (10, 8), "rapidocr_v5_latin": (10, 36)}  # the two RapidOCR points coincide
    for i, e in enumerate(ocr):
        ax.scatter(e["sec_per_page_mean"], e["macro_cer"], s=80, color=SERIES[i], marker=MARKERS[i],
                   edgecolors=SURFACE, linewidths=2, zorder=3, label=e["label"])
        ax.annotate(f"{e['label']}\n{e['sec_per_page_mean']:.2f} s/page · CER {e['macro_cer']:.3f}",
                    (e["sec_per_page_mean"], e["macro_cer"]), textcoords="offset points",
                    xytext=label_offsets.get(e["engine"], (8, 6)), fontsize=8, color=INK2)
    ax.set_xscale("log")
    ax.set_xlabel("Mean seconds per page (log scale, %d threads)" % THREADS)
    ax.set_ylabel("Macro-average CER")
    ax.set_ylim(0, max(e["macro_cer"] for e in ocr) * 1.35)
    fig.text(0.015, 0.985, "OCR speed vs accuracy (lower-left is better)", ha="left", va="top", fontsize=12, color=INK)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.94), ncol=3, frameon=False, fontsize=8,
               handletextpad=0.3, columnspacing=1.2)
    ax.margins(x=0.35)
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.savefig(RESULTS / "ocr_speed_vs_accuracy.png")
    plt.close(fig)

    # 3) PDF extractors: small multiples (CER | ms per page), single series color, value labels at bar tips
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2), dpi=200, sharey=True)
    labels = [e["label"] for e in pdf]
    ypos = list(range(len(pdf)))[::-1]
    panels = ((axes[0], [e["macro_cer"] for e in pdf], "Macro CER (lower is better)", "{:.3f}"),
              (axes[1], [e["sec_per_page_mean"] * 1000 for e in pdf], "Mean ms per page (lower is better)", "{:.1f}"))
    for ax, values, xlabel, fmt in panels:
        style_axes(ax)
        ax.barh(ypos, values, height=0.45, color=SERIES[0])
        for y, v in zip(ypos, values):
            ax.annotate(fmt.format(v), (v, y), textcoords="offset points", xytext=(4, 0), va="center",
                        fontsize=8, color=INK2)
        ax.set_xlabel(xlabel)
        ax.set_xlim(0, max(values) * 1.25)
    axes[0].set_yticks(ypos)
    axes[0].set_yticklabels(labels)
    fig.suptitle("PDF text-layer extraction: accuracy and speed", x=0.02, ha="left", fontsize=12, color=INK)
    fig.tight_layout()
    fig.savefig(RESULTS / "pdf_extractors.png")
    plt.close(fig)


# ---------------------------------------------------------------- orchestration

def worker(name: str) -> None:
    samples = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    print(f"== {name}", flush=True)
    result = bench_ocr(name, samples) if name in OCR_ENGINES else bench_pdf(name, samples)
    result["footprint_mb"] = footprint_mb(name)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"raw_{name}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--only", help="comma-separated candidate names")
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    if not args.summarize:
        names = args.only.split(",") if args.only else PDF_EXTRACTORS + OCR_ENGINES
        for name in names:
            t0 = time.perf_counter()
            subprocess.run([sys.executable, __file__, "--worker", name], check=True)
            print(f"   {name} finished in {time.perf_counter() - t0:.0f}s", flush=True)
    summarize()
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
