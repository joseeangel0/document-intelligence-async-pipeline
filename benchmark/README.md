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
