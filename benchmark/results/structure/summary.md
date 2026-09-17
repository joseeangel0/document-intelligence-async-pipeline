# Structure benchmark summary

Threads per candidate: 4. Heading F1 matches heading text with level tolerance ±1. Table F1 is cell-level (rows matched greedily, all tables of a document pooled). CER is computed after stripping Markdown syntax. Macro = mean over the synthetic document types (which have ground truth) where the candidate produced output. Scanned inputs and photos need OCR; candidates without OCR score 0 there. Real documents (public-domain IRS PDFs) have no structure ground truth: they are scored only by word recall against the publisher's text layer (BoW F1), to catch silently dropped text.

## Overall

| Candidate | Coverage | Heading F1 | Heading recall (any level) | List recall | Table cell F1 | CER | BoW F1 | s/page | Real docs BoW F1 (min) | Real s/page | Cold start s | Peak RSS MB | Install MB | License |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PyMuPDF get_text (baseline) | 9/11 | 0.00 | 0.00 | 0.42 | 0.00 | 0.700 | 0.33 | 0.002 | 1.00 (1.00) | 0.015 | 0.07 | 55 | 50 | AGPL-3.0 / commercial |
| pymupdf4llm 0.0.24 | 9/11 | 0.41 | 0.34 | 0.44 | 0.32 | 0.668 | 0.33 | 0.025 | 0.92 (0.81) | 0.173 | 0.07 | 89 | 51 | AGPL-3.0 / commercial |
| pymupdf4llm 0.0.24 + coverage guard | 9/11 | 0.41 | 0.34 | 0.44 | 0.32 | 0.668 | 0.33 | 0.025 | 0.98 (0.95) | 0.182 | 0.08 | 82 | 51 | AGPL-3.0 / commercial |
| pymupdf4llm 1.28 (+layout) | 9/11 | 0.82 | 0.88 | 1.00 | 0.34 | 0.453 | 0.56 | 0.446 | 0.98 (0.96) | 0.369 | 0.52 | 869 | 257 | AGPL-3.0 / commercial |
| MarkItDown 0.1.7 | 11/11 | 0.33 | 0.33 | 0.61 | 0.46 | 0.525 | 0.50 | 0.038 | 0.98 (0.97) | 0.143 | 4.65 | 218 | 211 | MIT (pdfminer MIT, mammoth BSD) |
| Docling 2.128 (+RapidOCR) | 11/11 | 1.00 | 1.00 | 1.00 | 1.00 | 0.009 | 0.99 | 4.409 | 0.98 (0.95) | 2.039 | 1.77 | 3305 | 2193 | MIT (models: Apache-2.0 / CDLA) |
| docintel service (API) | 11/11 | 0.87 | 0.83 | 0.90 | 0.50 | 0.238 | 1.00 | 0.798 | 0.98 (0.96) | 0.188 | – | – | – | this project |
| docintel service, layout pipeline (API) | 11/11 | 1.00 | 1.00 | 1.00 | 1.00 | 0.009 | 0.99 | 7.743 | 0.98 (0.95) | 5.077 | – | – | – | this project (Docling MIT inside) |

## Table cell F1 by document type

| Document type | PyMuPDF get_text (baseline) | pymupdf4llm 0.0.24 | pymupdf4llm 0.0.24 + coverage guard | pymupdf4llm 1.28 (+layout) | MarkItDown 0.1.7 | Docling 2.128 (+RapidOCR) | docintel service (API) | docintel service, layout pipeline (API) |
|---|---|---|---|---|---|---|---|---|
| digital_pdf | 0.00 | 0.95 | 0.95 | 0.97 | 0.83 | 1.00 | 1.00 | 1.00 |
| scanned_pdf | 0.00 | 0.00 | 0.00 | 0.04 | 0.00 | 1.00 | 0.00 | 1.00 |
| table_photo | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | 1.00 |
| docx | unsupported | unsupported | unsupported | unsupported | 1.00 | 1.00 | 1.00 | 1.00 |
| real_pdf | – | – | – | – | – | – | – | – |

## Heading F1 by document type

| Document type | PyMuPDF get_text (baseline) | pymupdf4llm 0.0.24 | pymupdf4llm 0.0.24 + coverage guard | pymupdf4llm 1.28 (+layout) | MarkItDown 0.1.7 | Docling 2.128 (+RapidOCR) | docintel service (API) | docintel service, layout pipeline (API) |
|---|---|---|---|---|---|---|---|---|
| digital_pdf | 0.00 | 0.81 | 0.81 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 |
| scanned_pdf | 0.00 | 0.00 | 0.00 | 0.63 | 0.00 | 1.00 | 0.62 | 1.00 |
| table_photo | – | – | – | – | – | – | – | – |
| docx | unsupported | unsupported | unsupported | unsupported | 1.00 | 1.00 | 1.00 | 1.00 |
| real_pdf | – | – | – | – | – | – | – | – |

## List-item recall by document type

| Document type | PyMuPDF get_text (baseline) | pymupdf4llm 0.0.24 | pymupdf4llm 0.0.24 + coverage guard | pymupdf4llm 1.28 (+layout) | MarkItDown 0.1.7 | Docling 2.128 (+RapidOCR) | docintel service (API) | docintel service, layout pipeline (API) |
|---|---|---|---|---|---|---|---|---|
| digital_pdf | 0.83 | 0.89 | 0.89 | 1.00 | 0.83 | 1.00 | 1.00 | 1.00 |
| scanned_pdf | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | 1.00 | 0.71 | 1.00 |
| table_photo | – | – | – | – | – | – | – | – |
| docx | unsupported | unsupported | unsupported | unsupported | 1.00 | 1.00 | 1.00 | 1.00 |
| real_pdf | – | – | – | – | – | – | – | – |

## CER (Markdown stripped) by document type

| Document type | PyMuPDF get_text (baseline) | pymupdf4llm 0.0.24 | pymupdf4llm 0.0.24 + coverage guard | pymupdf4llm 1.28 (+layout) | MarkItDown 0.1.7 | Docling 2.128 (+RapidOCR) | docintel service (API) | docintel service, layout pipeline (API) |
|---|---|---|---|---|---|---|---|---|
| digital_pdf | 0.101 | 0.005 | 0.005 | 0.101 | 0.101 | 0.006 | 0.006 | 0.006 |
| scanned_pdf | 1.000 | 1.000 | 1.000 | 0.584 | 1.000 | 0.031 | 0.333 | 0.031 |
| table_photo | 1.000 | 1.000 | 1.000 | 0.676 | 1.000 | 0.000 | 0.614 | 0.000 |
| docx | unsupported | unsupported | unsupported | unsupported | 0.000 | 0.000 | 0.000 | 0.000 |
| real_pdf | 0.002 | 0.664 | 0.667 | 0.645 | 0.676 | 0.670 | 0.663 | 0.670 |

## Seconds per page by document type

| Document type | PyMuPDF get_text (baseline) | pymupdf4llm 0.0.24 | pymupdf4llm 0.0.24 + coverage guard | pymupdf4llm 1.28 (+layout) | MarkItDown 0.1.7 | Docling 2.128 (+RapidOCR) | docintel service (API) | docintel service, layout pipeline (API) |
|---|---|---|---|---|---|---|---|---|
| digital_pdf | 0.003 | 0.025 | 0.026 | 0.147 | 0.030 | 3.398 | 0.046 | 6.913 |
| scanned_pdf | 0.000 | 0.023 | 0.024 | 0.850 | 0.015 | 7.601 | 2.167 | 11.543 |
| table_photo | 0.001 | 0.010 | 0.011 | 0.792 | 0.007 | 4.358 | 1.060 | 6.180 |
| docx | unsupported | unsupported | unsupported | unsupported | 0.128 | 0.055 | 0.050 | 0.060 |
| real_pdf | 0.016 | 0.149 | 0.159 | 0.355 | 0.134 | 1.911 | 0.169 | 4.910 |

## Per sample

| Candidate | Sample | Status | Heading F1 | List recall | Table F1 | Tables found/expected | Headings found | CER | BoW F1 | s/page |
|---|---|---|---|---|---|---|---|---|---|---|
| PyMuPDF get_text (baseline) | report_en | ok | 0.00 | 1.00 | 0.00 | 0/3 | 0 | 0.019 | 0.99 | 0.004 |
| PyMuPDF get_text (baseline) | informe_es | ok | 0.00 | 0.67 | 0.00 | 0/1 | 0 | 0.285 | 1.00 | 0.002 |
| PyMuPDF get_text (baseline) | invoice | ok | 0.00 | – | 0.00 | 0/2 | 0 | 0.000 | 1.00 | 0.002 |
| PyMuPDF get_text (baseline) | report_en_scanned | ok | 0.00 | 0.00 | 0.00 | 0/3 | 0 | 1.000 | 0.00 | 0.000 |
| PyMuPDF get_text (baseline) | invoice_scanned | ok | 0.00 | – | 0.00 | 0/2 | 0 | 1.000 | 0.00 | 0.000 |
| PyMuPDF get_text (baseline) | table_photo | ok | – | – | 0.00 | 0/1 | 0 | 1.000 | 0.00 | 0.001 |
| PyMuPDF get_text (baseline) | docx_plan_en | unsupported | – | – | – | – | – | – | – | – |
| PyMuPDF get_text (baseline) | docx_politica_es | unsupported | – | – | – | – | – | – | – | – |
| PyMuPDF get_text (baseline) | real_irs_fw9 | ok | – | – | – | 0 | 0 | 0.002 | 1.00 | 0.015 |
| PyMuPDF get_text (baseline) | real_irs_p1 | ok | – | – | – | 0 | 0 | 0.002 | 1.00 | 0.019 |
| PyMuPDF get_text (baseline) | real_irs_p15t_excerpt | ok | – | – | – | 0 | 0 | 0.002 | 1.00 | 0.013 |
| pymupdf4llm 0.0.24 | report_en | ok | 0.83 | 1.00 | 0.97 | 4/3 | 5 | 0.016 | 0.99 | 0.049 |
| pymupdf4llm 0.0.24 | informe_es | ok | 0.80 | 0.78 | 1.00 | 1/1 | 4 | 0.000 | 1.00 | 0.010 |
| pymupdf4llm 0.0.24 | invoice | ok | 0.80 | – | 0.89 | 1/2 | 2 | 0.000 | 1.00 | 0.017 |
| pymupdf4llm 0.0.24 | report_en_scanned | ok | 0.00 | 0.00 | 0.00 | 0/3 | 0 | 1.000 | 0.00 | 0.023 |
| pymupdf4llm 0.0.24 | invoice_scanned | ok | 0.00 | – | 0.00 | 0/2 | 0 | 1.000 | 0.00 | 0.022 |
| pymupdf4llm 0.0.24 | table_photo | ok | – | – | 0.00 | 0/1 | 0 | 1.000 | 0.00 | 0.010 |
| pymupdf4llm 0.0.24 | docx_plan_en | unsupported | – | – | – | – | – | – | – | – |
| pymupdf4llm 0.0.24 | docx_politica_es | unsupported | – | – | – | – | – | – | – | – |
| pymupdf4llm 0.0.24 | real_irs_fw9 | ok | – | – | – | 7 | 4 | 0.655 | 0.99 | 0.186 |
| pymupdf4llm 0.0.24 | real_irs_p1 | ok | – | – | – | 0 | 1 | 0.686 | 0.81 | 0.067 |
| pymupdf4llm 0.0.24 | real_irs_p15t_excerpt | ok | – | – | – | 0 | 13 | 0.651 | 0.95 | 0.194 |
| pymupdf4llm 0.0.24 + coverage guard | report_en | ok | 0.83 | 1.00 | 0.97 | 4/3 | 5 | 0.016 | 0.99 | 0.050 |
| pymupdf4llm 0.0.24 + coverage guard | informe_es | ok | 0.80 | 0.78 | 1.00 | 1/1 | 4 | 0.000 | 1.00 | 0.010 |
| pymupdf4llm 0.0.24 + coverage guard | invoice | ok | 0.80 | – | 0.89 | 1/2 | 2 | 0.000 | 1.00 | 0.017 |
| pymupdf4llm 0.0.24 + coverage guard | report_en_scanned | ok | 0.00 | 0.00 | 0.00 | 0/3 | 0 | 1.000 | 0.00 | 0.024 |
| pymupdf4llm 0.0.24 + coverage guard | invoice_scanned | ok | 0.00 | – | 0.00 | 0/2 | 0 | 1.000 | 0.00 | 0.023 |
| pymupdf4llm 0.0.24 + coverage guard | table_photo | ok | – | – | 0.00 | 0/1 | 0 | 1.000 | 0.00 | 0.011 |
| pymupdf4llm 0.0.24 + coverage guard | docx_plan_en | unsupported | – | – | – | – | – | – | – | – |
| pymupdf4llm 0.0.24 + coverage guard | docx_politica_es | unsupported | – | – | – | – | – | – | – | – |
| pymupdf4llm 0.0.24 + coverage guard | real_irs_fw9 | ok | – | – | – | 7 | 4 | 0.655 | 0.99 | 0.194 |
| pymupdf4llm 0.0.24 + coverage guard | real_irs_p1 | ok | – | – | – | 0 | 3 | 0.694 | 1.00 | 0.079 |
| pymupdf4llm 0.0.24 + coverage guard | real_irs_p15t_excerpt | ok | – | – | – | 0 | 13 | 0.651 | 0.95 | 0.205 |
| pymupdf4llm 1.28 (+layout) | report_en | ok | 1.00 | 1.00 | 1.00 | 5/3 | 7 | 0.016 | 0.99 | 0.198 |
| pymupdf4llm 1.28 (+layout) | informe_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 6 | 0.285 | 1.00 | 0.104 |
| pymupdf4llm 1.28 (+layout) | invoice | ok | 1.00 | – | 0.90 | 1/2 | 3 | 0.000 | 1.00 | 0.138 |
| pymupdf4llm 1.28 (+layout) | report_en_scanned | ok | 0.46 | 1.00 | 0.09 | 4/3 | 6 | 0.661 | 0.37 | 0.909 |
| pymupdf4llm 1.28 (+layout) | invoice_scanned | ok | 0.80 | – | 0.00 | 1/2 | 2 | 0.506 | 0.61 | 0.791 |
| pymupdf4llm 1.28 (+layout) | table_photo | ok | – | – | 0.00 | 0/1 | 0 | 0.676 | 0.19 | 0.792 |
| pymupdf4llm 1.28 (+layout) | docx_plan_en | unsupported | – | – | – | – | – | – | – | – |
| pymupdf4llm 1.28 (+layout) | docx_politica_es | unsupported | – | – | – | – | – | – | – | – |
| pymupdf4llm 1.28 (+layout) | real_irs_fw9 | ok | – | – | – | 10 | 20 | 0.646 | 0.99 | 0.484 |
| pymupdf4llm 1.28 (+layout) | real_irs_p1 | ok | – | – | – | 0 | 24 | 0.647 | 1.00 | 0.303 |
| pymupdf4llm 1.28 (+layout) | real_irs_p15t_excerpt | ok | – | – | – | 2 | 13 | 0.641 | 0.96 | 0.276 |
| MarkItDown 0.1.7 | report_en | ok | 0.00 | 1.00 | 0.98 | 8/3 | 0 | 0.019 | 0.99 | 0.039 |
| MarkItDown 0.1.7 | informe_es | ok | 0.00 | 0.67 | 1.00 | 1/1 | 0 | 0.285 | 1.00 | 0.018 |
| MarkItDown 0.1.7 | invoice | ok | 0.00 | – | 0.51 | 1/2 | 0 | 0.000 | 1.00 | 0.034 |
| MarkItDown 0.1.7 | report_en_scanned | ok | 0.00 | 0.00 | 0.00 | 0/3 | 0 | 1.000 | 0.00 | 0.018 |
| MarkItDown 0.1.7 | invoice_scanned | ok | 0.00 | – | 0.00 | 0/2 | 0 | 1.000 | 0.00 | 0.012 |
| MarkItDown 0.1.7 | table_photo | ok | – | – | 0.00 | 0/1 | 0 | 1.000 | 0.00 | 0.007 |
| MarkItDown 0.1.7 | docx_plan_en | ok | 1.00 | 1.00 | 1.00 | 2/2 | 5 | 0.000 | 1.00 | 0.152 |
| MarkItDown 0.1.7 | docx_politica_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 4 | 0.000 | 1.00 | 0.104 |
| MarkItDown 0.1.7 | real_irs_fw9 | ok | – | – | – | 0 | 0 | 0.652 | 0.99 | 0.113 |
| MarkItDown 0.1.7 | real_irs_p1 | ok | – | – | – | 0 | 0 | 0.694 | 0.99 | 0.102 |
| MarkItDown 0.1.7 | real_irs_p15t_excerpt | ok | – | – | – | 0 | 0 | 0.681 | 0.97 | 0.187 |
| Docling 2.128 (+RapidOCR) | report_en | ok | 1.00 | 1.00 | 1.00 | 5/3 | 7 | 0.019 | 0.99 | 5.180 |
| Docling 2.128 (+RapidOCR) | informe_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 6 | 0.000 | 1.00 | 1.143 |
| Docling 2.128 (+RapidOCR) | invoice | ok | 1.00 | – | 1.00 | 2/2 | 3 | 0.000 | 1.00 | 3.870 |
| Docling 2.128 (+RapidOCR) | report_en_scanned | ok | 1.00 | 1.00 | 1.00 | 5/3 | 7 | 0.051 | 0.98 | 9.610 |
| Docling 2.128 (+RapidOCR) | invoice_scanned | ok | 1.00 | – | 1.00 | 2/2 | 3 | 0.010 | 0.99 | 5.591 |
| Docling 2.128 (+RapidOCR) | table_photo | ok | – | – | 1.00 | 1/1 | 0 | 0.000 | 1.00 | 4.358 |
| Docling 2.128 (+RapidOCR) | docx_plan_en | ok | 1.00 | 1.00 | 1.00 | 2/2 | 5 | 0.000 | 1.00 | 0.071 |
| Docling 2.128 (+RapidOCR) | docx_politica_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 4 | 0.000 | 1.00 | 0.039 |
| Docling 2.128 (+RapidOCR) | real_irs_fw9 | ok | – | – | – | 4 | 28 | 0.644 | 0.99 | 2.627 |
| Docling 2.128 (+RapidOCR) | real_irs_p1 | ok | – | – | – | 0 | 23 | 0.701 | 0.99 | 1.462 |
| Docling 2.128 (+RapidOCR) | real_irs_p15t_excerpt | ok | – | – | – | 2 | 14 | 0.664 | 0.95 | 1.645 |
| docintel service (API) | report_en | ok | 1.00 | 1.00 | 1.00 | 5/3 | 7 | 0.019 | 0.99 | 0.067 |
| docintel service (API) | informe_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 6 | 0.000 | 1.00 | 0.020 |
| docintel service (API) | invoice | ok | 1.00 | – | 1.00 | 2/2 | 3 | 0.000 | 1.00 | 0.050 |
| docintel service (API) | report_en_scanned | ok | 0.44 | 0.71 | 0.00 | 0/3 | 2 | 0.414 | 0.99 | 2.683 |
| docintel service (API) | invoice_scanned | ok | 0.80 | – | 0.00 | 0/2 | 2 | 0.253 | 1.00 | 1.650 |
| docintel service (API) | table_photo | ok | – | – | 0.00 | 0/1 | 0 | 0.614 | 1.00 | 1.060 |
| docintel service (API) | docx_plan_en | ok | 1.00 | 1.00 | 1.00 | 2/2 | 5 | 0.000 | 1.00 | 0.060 |
| docintel service (API) | docx_politica_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 4 | 0.000 | 1.00 | 0.040 |
| docintel service (API) | real_irs_fw9 | ok | – | – | – | 6 | 23 | 0.648 | 0.98 | 0.182 |
| docintel service (API) | real_irs_p1 | ok | – | – | – | 0 | 26 | 0.690 | 0.99 | 0.105 |
| docintel service (API) | real_irs_p15t_excerpt | ok | – | – | – | 0 | 21 | 0.650 | 0.96 | 0.222 |
| docintel service, layout pipeline (API) | report_en | ok | 1.00 | 1.00 | 1.00 | 5/3 | 7 | 0.019 | 0.99 | 11.863 |
| docintel service, layout pipeline (API) | informe_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 6 | 0.000 | 1.00 | 2.357 |
| docintel service, layout pipeline (API) | invoice | ok | 1.00 | – | 1.00 | 2/2 | 3 | 0.000 | 1.00 | 6.520 |
| docintel service, layout pipeline (API) | report_en_scanned | ok | 1.00 | 1.00 | 1.00 | 5/3 | 7 | 0.051 | 0.98 | 14.917 |
| docintel service, layout pipeline (API) | invoice_scanned | ok | 1.00 | – | 1.00 | 2/2 | 3 | 0.010 | 0.99 | 8.170 |
| docintel service, layout pipeline (API) | table_photo | ok | – | – | 1.00 | 1/1 | 0 | 0.000 | 1.00 | 6.180 |
| docintel service, layout pipeline (API) | docx_plan_en | ok | 1.00 | 1.00 | 1.00 | 2/2 | 5 | 0.000 | 1.00 | 0.070 |
| docintel service, layout pipeline (API) | docx_politica_es | ok | 1.00 | 1.00 | 1.00 | 1/1 | 4 | 0.000 | 1.00 | 0.050 |
| docintel service, layout pipeline (API) | real_irs_fw9 | ok | – | – | – | 4 | 28 | 0.644 | 0.99 | 4.582 |
| docintel service, layout pipeline (API) | real_irs_p1 | ok | – | – | – | 0 | 23 | 0.701 | 0.99 | 4.325 |
| docintel service, layout pipeline (API) | real_irs_p15t_excerpt | ok | – | – | – | 2 | 14 | 0.664 | 0.95 | 5.823 |
