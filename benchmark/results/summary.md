# Benchmark summary

Threads per engine: 4. CER/WER: lower is better. BoW F1 (reading-order-insensitive): higher is better. Macro averages are the mean over synthetic categories (real-document rows excluded).

## OCR engines — overall

| Engine | Macro CER | Macro WER | Macro BoW F1 | s/page mean | s/page p95 | Cold start s | Load RSS MB | Peak RSS MB | Install MB | License |
|---|---|---|---|---|---|---|---|---|---|---|
| Tesseract 5.3 | 0.214 | 0.230 | 0.826 | 0.28 | 0.56 | 0.08 | 27 | 252 | 15 | Apache-2.0 |
| Tesseract 5.3 + OpenCV prep | 0.080 | 0.126 | 0.912 | 0.33 | 0.73 | 0.10 | 49 | 348 | 88 | Apache-2.0 (+ OpenCV Apache-2.0) |
| RapidOCR PP-OCRv6 small | 0.001 | 0.005 | 0.997 | 0.91 | 1.64 | 0.34 | 126 | 1099 | 221 | Apache-2.0 (models Apache-2.0) |
| RapidOCR PP-OCRv5 latin | 0.002 | 0.006 | 0.997 | 0.94 | 1.69 | 0.34 | 106 | 1129 | 204 | Apache-2.0 (models Apache-2.0) |
| EasyOCR 1.7 | 0.043 | 0.138 | 0.966 | 6.96 | 13.00 | 2.73 | 455 | 6376 | 812 | Apache-2.0 |

## OCR — CER by category

| Category | Tesseract 5.3 | Tesseract 5.3 + OpenCV prep | RapidOCR PP-OCRv6 small | RapidOCR PP-OCRv5 latin | EasyOCR 1.7 |
|---|---|---|---|---|---|
| clean | 0.001 | 0.002 | 0.000 | 0.000 | 0.010 |
| blur | 0.004 | 0.012 | 0.002 | 0.009 | 0.106 |
| noise | 0.963 | 0.003 | 0.002 | 0.000 | 0.017 |
| rotated | 0.335 | 0.334 | 0.000 | 0.000 | 0.296 |
| lowres | 0.002 | 0.050 | 0.000 | 0.007 | 0.013 |
| photo | 0.514 | 0.008 | 0.000 | 0.000 | 0.014 |
| inverted | 0.002 | 0.281 | 0.000 | 0.005 | 0.009 |
| jpeg_q15 | 0.002 | 0.006 | 0.001 | 0.000 | 0.012 |
| two_column | 0.248 | 0.248 | 0.000 | 0.000 | 0.005 |
| small_font | 0.001 | 0.006 | 0.001 | 0.000 | 0.008 |
| invoice | 0.002 | 0.002 | 0.000 | 0.000 | 0.016 |
| scanned_pdf | 0.501 | 0.004 | 0.001 | 0.001 | 0.013 |
| real_150dpi | 0.002 | 0.025 | 0.000 | 0.000 | 0.013 |
| real_300dpi | 0.002 | 0.003 | 0.000 | 0.000 | 0.012 |

## OCR — WER by category

| Category | Tesseract 5.3 | Tesseract 5.3 + OpenCV prep | RapidOCR PP-OCRv6 small | RapidOCR PP-OCRv5 latin | EasyOCR 1.7 |
|---|---|---|---|---|---|
| clean | 0.005 | 0.009 | 0.000 | 0.000 | 0.059 |
| blur | 0.023 | 0.059 | 0.027 | 0.032 | 0.499 |
| noise | 0.982 | 0.023 | 0.009 | 0.000 | 0.118 |
| rotated | 0.343 | 0.338 | 0.000 | 0.000 | 0.360 |
| lowres | 0.014 | 0.214 | 0.000 | 0.022 | 0.078 |
| photo | 0.544 | 0.041 | 0.000 | 0.000 | 0.109 |
| inverted | 0.009 | 0.430 | 0.000 | 0.018 | 0.064 |
| jpeg_q15 | 0.018 | 0.032 | 0.018 | 0.000 | 0.096 |
| two_column | 0.304 | 0.304 | 0.000 | 0.000 | 0.030 |
| small_font | 0.003 | 0.034 | 0.007 | 0.000 | 0.051 |
| invoice | 0.006 | 0.006 | 0.000 | 0.000 | 0.111 |
| scanned_pdf | 0.502 | 0.018 | 0.005 | 0.005 | 0.084 |
| real_150dpi | 0.007 | 0.133 | 0.000 | 0.000 | 0.087 |
| real_300dpi | 0.007 | 0.016 | 0.000 | 0.000 | 0.069 |

## OCR — BoW F1 by category

| Category | Tesseract 5.3 | Tesseract 5.3 + OpenCV prep | RapidOCR PP-OCRv6 small | RapidOCR PP-OCRv5 latin | EasyOCR 1.7 |
|---|---|---|---|---|---|
| clean | 0.995 | 0.991 | 1.000 | 1.000 | 1.000 |
| blur | 0.982 | 0.947 | 0.987 | 0.987 | 0.678 |
| noise | 0.057 | 0.980 | 1.000 | 1.000 | 1.000 |
| rotated | 0.657 | 0.662 | 1.000 | 1.000 | 0.991 |
| lowres | 0.989 | 0.845 | 1.000 | 0.990 | 0.988 |
| photo | 0.597 | 0.972 | 1.000 | 1.000 | 0.995 |
| inverted | 0.991 | 0.602 | 1.000 | 0.990 | 1.000 |
| jpeg_q15 | 0.989 | 0.982 | 0.987 | 1.000 | 0.991 |
| two_column | 0.995 | 0.994 | 1.000 | 1.000 | 1.000 |
| small_font | 0.997 | 0.984 | 0.995 | 1.000 | 0.988 |
| invoice | 0.996 | 0.998 | 1.000 | 1.000 | 0.960 |
| scanned_pdf | 0.663 | 0.992 | 0.998 | 1.000 | 0.998 |
| real_150dpi | 0.997 | 0.962 | 1.000 | 1.000 | 0.975 |
| real_300dpi | 0.997 | 0.994 | 1.000 | 1.000 | 0.972 |

## OCR — seconds per page by category

| Category | Tesseract 5.3 | Tesseract 5.3 + OpenCV prep | RapidOCR PP-OCRv6 small | RapidOCR PP-OCRv5 latin | EasyOCR 1.7 |
|---|---|---|---|---|---|
| clean | 0.24 | 0.25 | 0.79 | 0.65 | 6.04 |
| blur | 0.30 | 0.33 | 0.71 | 1.23 | 6.39 |
| noise | 0.22 | 0.25 | 0.76 | 0.65 | 6.15 |
| rotated | 0.18 | 0.19 | 0.75 | 0.61 | 6.56 |
| lowres | 0.19 | 0.23 | 0.76 | 0.86 | 1.92 |
| photo | 0.25 | 0.24 | 0.66 | 0.60 | 3.79 |
| inverted | 0.34 | 0.39 | 0.76 | 1.11 | 5.85 |
| jpeg_q15 | 0.22 | 0.24 | 0.74 | 0.63 | 2.47 |
| two_column | 0.35 | 0.36 | 1.07 | 1.03 | 9.04 |
| small_font | 0.29 | 0.33 | 1.20 | 0.97 | 5.34 |
| invoice | 0.24 | 0.32 | 0.83 | 0.90 | 8.04 |
| scanned_pdf | 0.20 | 0.28 | 0.78 | 0.78 | 5.37 |
| real_150dpi | 0.48 | 0.82 | 1.68 | 1.91 | 12.41 |
| real_300dpi | 0.65 | 0.82 | 1.90 | 2.17 | 28.67 |

## PDF text-layer extractors — overall

| Extractor | Macro CER | Macro WER | Macro BoW F1 | ms/page mean | ms/page p95 | Import s | Install MB | License |
|---|---|---|---|---|---|---|---|---|
| PyMuPDF | 0.116 | 0.140 | 1.000 | 0.8 | 1.3 | 0.08 | 49.9 | AGPL-3.0 or commercial |
| PyMuPDF (sort=True) | 0.093 | 0.113 | 1.000 | 2.6 | 4.4 | 0.08 | 49.9 | AGPL-3.0 or commercial |
| pypdfium2 | 0.222 | 0.273 | 1.000 | 0.5 | 0.8 | 0.10 | 6.3 | Apache-2.0 / BSD-3 (PDFium) |
| pdfplumber | 0.220 | 0.388 | 0.872 | 11.6 | 25.8 | 0.13 | 14.3 | MIT |
| pypdf | 0.215 | 0.912 | 0.860 | 2.8 | 13.0 | 0.11 | 2.6 | BSD-3-Clause |
| pdfminer.six | 0.292 | 0.865 | 0.860 | 10.0 | 27.1 | 0.09 | 7.6 | MIT |

## PDF extractors — by category (CER / ms per page)

`scanned_pdf` has no text layer: the column shows characters returned (0 = correctly signals OCR is needed).

| Category | PyMuPDF | PyMuPDF (sort=True) | pypdfium2 | pdfplumber | pypdf | pdfminer.six |
|---|---|---|---|---|---|---|
| scanned_pdf | 0 chars | 0 chars | 0 chars | 0 chars | 0 chars | 0 chars |
| pdf_single | 0.000 / 0.9 ms | 0.000 / 3.0 ms | 0.000 / 0.5 ms | 0.000 / 10.0 ms | 0.000 / 1.4 ms | 0.000 / 4.8 ms |
| pdf_two_column | 0.000 / 0.9 ms | 0.000 / 4.0 ms | 0.000 / 0.6 ms | 0.000 / 14.5 ms | 0.000 / 1.5 ms | 0.000 / 6.4 ms |
| pdf_table | 0.000 / 0.7 ms | 0.000 / 1.3 ms | 0.000 / 0.4 ms | 0.000 / 5.3 ms | 0.000 / 1.4 ms | 0.648 / 4.3 ms |
| pdf_out_of_order | 0.813 / 0.9 ms | 0.653 / 3.4 ms | 0.813 / 0.5 ms | 0.653 / 11.9 ms | 0.813 / 1.6 ms | 0.147 / 5.9 ms |
| pdf_char_positioned | 0.000 / 1.3 ms | 0.000 / 2.9 ms | 0.000 / 0.8 ms | 0.145 / 26.2 ms | 0.690 / 13.1 ms | 0.000 / 23.9 ms |
| pdf_rotated | 0.000 / 0.8 ms | 0.000 / 2.6 ms | 0.741 / 0.5 ms | 0.741 / 8.1 ms | 0.000 / 1.0 ms | 1.249 / 29.4 ms |
| pdf_multipage | 0.000 / 0.5 ms | 0.000 / 5.5 ms | 0.000 / 0.6 ms | 0.000 / 24.4 ms | 0.000 / 1.6 ms | 0.000 / 8.4 ms |
| real_pdf | 1.5 ms | 4.1 ms | 0.8 ms | 19.4 ms | 5.5 ms | 11.3 ms |
