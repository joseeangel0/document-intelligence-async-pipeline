# Parser & OCR benchmark

This benchmark backs the Part 3 parser choices with numbers. It compares PDF text-layer
extractors and on-device OCR engines on a deterministic dataset where the correct text is known.

## Reproduce

Run from the project root (everything is CPU-only and offline once the image is built):

```bash
docker build -t docintel-bench benchmark/
docker run --rm -v "$PWD/benchmark":/bench docintel-bench              # generate dataset + run all + summarize
```

Useful variants:

```bash
docker run --rm -v "$PWD/benchmark":/bench docintel-bench python generate_dataset.py
docker run --rm -v "$PWD/benchmark":/bench docintel-bench python run_benchmark.py --only tesseract,pymupdf
docker run --rm -v "$PWD/benchmark":/bench docintel-bench python run_benchmark.py --summarize   # tables + charts only
docker run --rm -e BENCH_THREADS=2 -v "$PWD/benchmark":/bench docintel-bench python run_benchmark.py
```

Outputs in `results/`:

| File | Content |
|---|---|
| `summary.md` | report-ready tables (overall + per category) |
| `summary.csv` | one row per candidate × category |
| `engines.csv` | one row per candidate (macro metrics, speed, memory, footprint, license) |
| `results.json` | raw per-sample records, including a 300-char preview of each output |
| `ocr_cer_by_category.png`, `ocr_speed_vs_accuracy.png`, `pdf_extractors.png` | charts |
| `run.log` | console log of the last full run |

## Candidates

| Kind | Candidate | Notes |
|---|---|---|
| PDF text layer | PyMuPDF 1.24 (`get_text()`, and `get_text(sort=True)`) | MuPDF bindings |
| PDF text layer | pypdfium2 4.30 | Google PDFium bindings |
| PDF text layer | pdfplumber 0.11 | built on pdfminer.six |
| PDF text layer | pypdf 5.1 | pure Python |
| PDF text layer | pdfminer.six 20231228 | pure Python |
| OCR | Tesseract 5.3 (Debian package) via pytesseract, `--oem 1 --psm 3`, `eng+spa` | LSTM engine, raw image |
| OCR | Tesseract 5.3 + OpenCV preprocessing | illumination flattening (divide by dilated/median background), median 3×3, Otsu |
| OCR | RapidOCR 3.9 (ONNX Runtime), default PP-OCRv6 small det/rec | PaddleOCR models exported to ONNX, `use_cls=False` |
| OCR | RapidOCR 3.9, PP-OCRv5 mobile det + **latin** rec | Latin dictionary covers á é ñ ¿ ¡, `use_cls=False` |
| OCR | EasyOCR 1.7 (PyTorch CPU), `['en', 'es']`, `quantize=False` | CRAFT detection + CRNN recognition |

docTR was left out: it adds another PyTorch stack and wasn't expected to change the decision.
Heavier layout-aware VLM or transformer pipelines (Docling, Marker, Nougat) were also left out,
because the service must run on modest CPU-only machines.

## Dataset (`generate_dataset.py`, seed 1234)

A small hand-written corpus of English and Spanish paragraphs (with accents, ñ, ¿¡, emails, phone numbers,
amounts and dates) plus generated invoice lines.

* **Images, 3 samples per category.** The page is rendered at a size that matches ~10 pt at 300 dpi.
  The degradations are deliberately harsh, because clean renders gave near-zero error for every engine.
  * `clean`
  * `blur` (Gaussian r=3)
  * `noise` (σ=60 plus 4 % salt-and-pepper noise)
  * `rotated` (1–4°)
  * `lowres` (downscaled to 40 %, about 120 dpi)
  * `jpeg_q15` (50 % scale, JPEG quality 15)
  * `photo` (perspective warp, uneven lighting and shadow, low contrast, warm paper tint, blur)
  * `inverted` (white text on a coloured band)
  * `two_column`
  * `small_font` (~6 pt)
  * `invoice`
* **Scanned PDFs.** These are image-only, 2 pages each (a clean page plus a noisy page). The OCR benchmark
  rasterizes them at 300 dpi with PyMuPDF, as the worker does.
* **Born-digital PDFs** (reportlab):
  * `pdf_single`
  * `pdf_two_column`
  * `pdf_table`
  * `pdf_multipage` (20 pages)
  * `pdf_out_of_order`: the content stream order differs from the visual reading order, as in many design-tool exports.
  * `pdf_char_positioned`: every glyph is placed on its own and no space characters are written, so the extractor has to infer word breaks.
  * `pdf_rotated`: the page has `/Rotate 90`.
* **Real document.** This is the assignment PDF (`data/real/U1T02.pdf`). Its pages are rasterized at 150 and 300 dpi,
  and PyMuPDF's text layer serves as the reference for OCR. The real PDF is only timed for extractors,
  since no independent ground truth exists for it.

## Key results (full tables in `results/summary.md`)

| OCR engine | Macro CER | Macro WER | s/page mean (p95) | Cold start | Peak RSS | Install |
|---|---|---|---|---|---|---|
| Tesseract 5.3 (raw) | 0.214 | 0.230 | 0.28 (0.56) | 0.08 s | 252 MB | 15 MB |
| Tesseract 5.3 + OpenCV prep | 0.080 | 0.126 | 0.33 (0.73) | 0.10 s | 348 MB | 88 MB |
| **RapidOCR PP-OCRv6 small** | **0.001** | **0.005** | 0.91 (1.64) | 0.34 s | 1.1 GB | 221 MB |
| RapidOCR PP-OCRv5 latin | 0.002 | 0.006 | 0.94 (1.69) | 0.34 s | 1.1 GB | 204 MB |
| EasyOCR 1.7 | 0.043 | 0.138 | 6.96 (13.0) | 2.7 s | 6.4 GB | 812 MB |

| PDF extractor | Macro CER | ms/page | Rotated | Glyph-positioned | Out-of-order stream | License |
|---|---|---|---|---|---|---|
| **PyMuPDF `sort=True`** | **0.093** | 2.6 | 0.000 | 0.000 | 0.653 | AGPL-3.0 |
| PyMuPDF | 0.116 | 0.8 | 0.000 | 0.000 | 0.813 | AGPL-3.0 |
| pypdfium2 | 0.222 | 0.5 | 0.741 | 0.000 | 0.813 | Apache/BSD |
| pdfplumber | 0.220 | 11.6 | 0.741 | 0.145 | 0.653 | MIT |
| pypdf | 0.215 | 2.8 | 0.000 | 0.690 | 0.813 | BSD-3 |
| pdfminer.six | 0.292 | 10.0 | 1.249 | 0.000 | 0.147 | MIT |

Every extractor scores 0.000 CER on the simple single-column, two-column, table and 20-page PDFs, except pdfminer.six
on tables (0.648). Every extractor also returns 0 characters for scanned PDFs, so a per-page "no text layer, use OCR"
check is reliable.

## Metrics & method

* **Text normalization before scoring.** NFKC, curly quotes and dashes mapped to ASCII, bullets removed,
  and whitespace collapsed. Line wrapping and ligatures (ﬁ) therefore don't count as errors, but case,
  accents and punctuation still do.
* **CER** is the character Levenshtein distance divided by the reference length. **WER** is the same on
  whitespace tokens. Both are computed with `rapidfuzz`.
* **BoW F1** is the F1 score over lower-cased word multisets. It ignores reading order, so it separates
  "recognized the words" from "put them in the right order" (multi-column and out-of-order cases).
* **Macro** metrics average the per-category means, so every condition counts equally. Real-document
  rows are excluded from the macro average.
* **Speed**
  * Each candidate runs in a **separate subprocess**.
  * **Cold start** is the import time plus the model load time.
  * One warm-up call runs first. After it, every OCR page is timed once and every PDF is timed as the
    median of 3 runs.
  * The speed metrics are the mean and p95 seconds per page.
* **Memory** has two measures. The first is the RSS delta after the engine loads. The second is peak RSS
  (`ru_maxrss` for the process plus its children, so the `tesseract` binary counts too).
* **Threads.** Every engine is limited to the same **4 threads** (`BENCH_THREADS`). This is applied through
  `OMP_NUM_THREADS` and `OMP_THREAD_LIMIT` (Tesseract), ONNX Runtime `intra_op_num_threads` (RapidOCR),
  and `torch.set_num_threads` (EasyOCR).
* **Hardware.** Apple Silicon (arm64) running Docker via OrbStack, 10 CPUs and 7.8 GB RAM visible to Docker.
* **Install footprint** is the size of the Python distributions each candidate needs, plus models and
  apt packages (for Tesseract). It is approximate, because shared dependencies such as numpy are not counted.
* **OCR reading order.** RapidOCR and EasyOCR return unordered text boxes, so both get the same
  post-processing (`reading_order()`). It first deskews the box corners by the median line angle.
  It then runs a recursive XY-cut, where a column split needs a gap of at least one line height.
  Finally, boxes are grouped into lines. Tesseract does its own page segmentation (`psm 3`).

## Gotchas found while benchmarking

* **The RapidOCR angle classifier garbles upright lines.** With the default `use_cls=True`, the 0/180° line
  classifier flipped some upright lines. PP-OCRv6 then returned garbage for them ("Plea oa l e t ave"), and the latin
  model dropped them. On a 5-image probe, turning the classifier off took per-image CER from as high as 0.16–0.40
  down to ≤ 0.007. Handle whole-page orientation separately, for example with Tesseract OSD or by trying 180° when
  confidence is low.
* **Raw Tesseract returns empty text on strong noise and fails on uneven lighting.** It uses a global Otsu
  threshold. Flattening the illumination first fixes both cases, but it breaks white-on-colour text
  (inverted CER rises from 0.00 to about 0.3), so preprocessing is a trade-off and not free.
* **EasyOCR crashes with SIGILL.** On torch 2.8 (arm64, OrbStack), EasyOCR's default dynamically quantized LSTM
  dies with `SIGILL`. `quantize=False` avoids it.
* **Several extractors mishandle rotated pages.** On `/Rotate 90` pages, pypdfium2 returns the lines in reverse order,
  pdfplumber puts one word on each line, and pdfminer.six emits one character per line, which is also slow at about 30 ms/page. PyMuPDF and pypdf handle the rotation correctly.
* **The benchmark runs `rapidocr` 3.x, not the old `rapidocr_onnxruntime` 1.x.** The old package bundles Chinese-only
  PP-OCRv4 recognition, which has no Spanish accents. `rapidocr` 3.x downloads models from ModelScope into
  `site-packages/rapidocr/models/` on first use, so bake them into the image at build time.

## Limitations

* The data is mostly synthetic, with clean fonts. Real scans, handwriting, stamps, complex tables and
  photos taken at an angle are harder, so absolute error rates will be higher in production. What the
  benchmark supports is the *relative* ranking and the speed/memory trade-offs.
* The samples are few: 3 per image category and 2 per PDF category. Small CER differences below
  about 0.01 are noise.
* The reference text for the real document comes from its own text layer, so it is only used to score OCR.
* The results are CPU-only on arm64. x86 with AVX-512, or a GPU, would change the absolute speed,
  especially for EasyOCR.

---

# Structure / LLM-readiness benchmark

Text that is correct but flat is not enough for GenAI. Retrieval and LLM prompts work better when headings, lists and
tables survive as Markdown. This second benchmark measures **how much document structure each converter preserves**,
how fast it runs and what it costs to deploy.

## Reproduce

```bash
docker build -f benchmark/Dockerfile.structure -t docintel-bench-structure benchmark/   # ~5 GB (Docling + torch CPU + 730 MB of models)
docker run --rm -v "$PWD/benchmark":/bench docintel-bench-structure python fetch_real_structure.py      # optional, needs network
docker run --rm -v "$PWD/benchmark":/bench docintel-bench-structure python generate_structure_dataset.py
docker run --rm --add-host host.docker.internal:host-gateway -v "$PWD/benchmark":/bench \
    docintel-bench-structure python run_structure_benchmark.py         # all candidates; docintel only if the stack is up
docker run --rm -v "$PWD/benchmark":/bench docintel-bench-structure python run_structure_benchmark.py --summarize
```

Outputs are written to `results/structure/`:

* `summary.md`: overall table, per-type tables and a per-sample table
* `summary.csv` and `results.json`: per sample, with an output preview
* `outputs/<candidate>/<sample>.md`: the raw Markdown from each candidate
* `raw_*.json`: timings, cold start, peak RSS and install size
* `structure_by_doc_type.png` and `structure_vs_speed.png`: charts

## Dataset (`generate_structure_dataset.py`, seed 4321)

Each document is written once as a list of blocks (heading with level, paragraph, bullet list, table). That list is
rendered to a file and also serialized as the ground-truth Markdown.

| Type | Samples | What it exercises |
|---|---|---|
| `digital_pdf` | 3 | English report with H1/H2/H3, bullet lists, a grid table, a borderless table and a 60-row table spanning pages; Spanish report with a real **two-column** page; invoice with a line-items table and a borderless totals table |
| `scanned_pdf` | 2 | Report and invoice rasterized at 200 dpi, with noise, slight skew and JPEG compression (image-only PDFs) |
| `table_photo` | 1 | Invoice table photographed: tint, uneven light, 1.8° skew, blur, noise |
| `docx` | 2 | python-docx with Heading 1–3, List Bullet and Table Grid (EN and ES) |
| `real_pdf` | 3 | **Real public-domain documents** (US federal works, 17 U.S.C. §105): IRS Form W-9, Publication 1 and the first 6 pages of Publication 15-T. These have no hand-made structure ground truth, so they are scored only by word recall (BoW F1) against the publisher's text layer. This catches converters that silently drop text. |

## Metrics

* **Heading F1:** heading text (fuzzy ≥ 90) with the level within ±1. "Any level" recall ignores the level.
* **Table cell F1:** Markdown pipe tables and HTML `<table>`s are parsed into rows. Predicted rows are matched greedily to
  ground-truth rows by shared cells. All tables of a document are pooled, so a table split across pages is not
  penalised, and a header repeated on each page counts once.
* **List-item recall:** list lines (`-`, `*`, `1.`) that match a ground-truth item.
* **CER:** computed after stripping Markdown and inline HTML (`<sup>`, `<br>`…).
* **BoW F1:** word recall and precision, independent of reading order.
* **Cost:** s/page (warm, after warming up on the smallest digital and scanned inputs), cold start, peak RSS, and
  install size (the dependency closure plus models).
* **Macro average:** mean over the synthetic document types. Candidates without OCR score 0 on scans and photos, which
  is the real behaviour a user would get.

## Results (4 threads, arm64)

| Candidate | Heading F1 | List recall | Table cell F1 | CER | s/page | Real docs BoW F1 (min) | Peak RSS | Install |
|---|---|---|---|---|---|---|---|---|
| PyMuPDF `get_text` (baseline) | 0.00 | 0.42 | 0.00 | 0.700 | 0.002 | 1.00 (1.00) | 55 MB | 50 MB |
| pymupdf4llm 0.0.24 | 0.41 | 0.44 | 0.32 | 0.668 | 0.025 | 0.92 (**0.81**) | 89 MB | 51 MB |
| pymupdf4llm 0.0.24 + coverage guard | 0.41 | 0.44 | 0.32 | 0.668 | 0.025 | 0.98 (0.95) | 82 MB | 51 MB |
| pymupdf4llm 1.28 (+ layout model) | 0.82 | 1.00 | 0.34 | 0.453 | 0.446 | 0.98 (0.96) | 869 MB | 257 MB |
| MarkItDown 0.1.7 | 0.33 | 0.61 | 0.46 | 0.525 | 0.038 | 0.98 (0.97) | 218 MB | 211 MB |
| **Docling 2.128 + RapidOCR** | **1.00** | **1.00** | **1.00** | **0.009** | 4.409 | 0.98 (0.95) | 3.3 GB | 2.2 GB |
| docintel service (API, at run time) | 0.64 | 0.57 | 0.49 | 0.262 | 0.846 | 0.98 (0.96) | – | – |

These macro scores average in the scans and photos, which hurts every candidate without OCR. Per document type:

| Table cell F1 / Heading F1 | digital_pdf | scanned_pdf | table_photo | docx |
|---|---|---|---|---|
| pymupdf4llm 0.0.24 | 0.95 / 0.81 | 0.00 / 0.00 | 0.00 | unsupported |
| pymupdf4llm 1.28 (+layout) | 0.97 / 1.00 | 0.04 / 0.63 | 0.00 | unsupported |
| MarkItDown 0.1.7 | 0.83 / 0.00 | 0.00 / 0.00 | 0.00 | 1.00 / 1.00 |
| Docling 2.128 + RapidOCR | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 | 1.00 / 1.00 |
| docintel service | 0.96 / 0.92 | 0.00 / 0.00 | 0.00 | 1.00 / 1.00 |

Speed on digital PDFs: pymupdf4llm 0.0.24 takes 0.025 s/page, 1.28 takes 0.147, and Docling takes 3.4. Docling slows to
7.6 s/page on scans. The full tables are in `results/structure/summary.md`.

## Gotchas found while benchmarking

* **pymupdf4llm silently drops text drawn over vector graphics.** On the cover of IRS Publication 1 it kept 18 of
  642 words. `ignore_graphics=True` recovers all of them but disables line-based table detection on that page. The
  "coverage guard" candidate handles this per page:
  1. compare Markdown word count with `page.get_text()`;
  2. below 90%, retry with `ignore_graphics=True`;
  3. if still below 90%, fall back to plain text.

  The guard costs nothing on normal pages.
* **pymupdf4llm version compatibility.** 0.0.24 is the newest release compatible with the app's `PyMuPDF==1.25.5`
  (requires `pymupdf>=1.25.5`). 0.0.25 needs ≥1.26.1, and 1.28.x pins `pymupdf==1.28.2` plus `pymupdf_layout`.
  1.28 finds headings and lists much better, but reads two-column pages across the columns (CER 0.285, like plain
  `sort=True`), is 6× slower, and adds 200 MB and ~800 MB RSS. It also OCRs image-only pages with Tesseract when
  available, poorly (CER 0.58).
* **pymupdf4llm list artifacts.** When bullet glyph metrics differ from the text, 1.28 wraps list items in
  `<sup>…</sup>` and 0.0.24 wraps them in `[…]`. The first dataset version triggered this with a 12 pt bullet next to
  10 pt text, and 1.28 still does it on the real IRS W-9. The generator now uses the same size, and the scorer strips
  inline HTML.
* **Docling OCR engine matters more than Docling itself.** With `TesseractCliOcrOptions` the table structure model
  returned **empty cells** on scans, and a photo produced an empty document (table F1 0.00–0.20). With
  `RapidOcrOptions` both reached 1.00. `do_cell_matching=False` produced empty grids with both engines, so keep the
  default `True`. RapidOCR models must be in the artifacts path:
  `docling-tools models download layout tableformer rapidocr -o <dir>`, then
  `PdfPipelineOptions(artifacts_path=<dir>)`.
* **MarkItDown** finds PDF tables (pdfplumber) but no PDF headings, and cannot OCR. It returns empty text for scans
  and images unless an LLM client is configured, which is not on-device. Its DOCX path (mammoth → HTML → Markdown) is
  perfect on headings, lists and tables.
* **Real-document CER is not meaningful** because reading order differs from the publisher's text layer. Use BoW F1.

## Structure-benchmark limitations

* Only 11 inputs (8 synthetic + 3 real). Structure ground truth exists only for the synthetic ones, which use clean
  fonts and simple layouts, so the ranking is more trustworthy than the absolute scores.
* The docintel row is a snapshot of the service while its Markdown output was still being developed. Re-run with
  `--only docintel` after changes.
* CPU-only timings. Docling's layout and table models are much faster on a GPU.
