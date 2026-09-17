# Benchmark image: every candidate parser/OCR engine in one CPU-only image.
# Build context: the benchmark/ directory.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa \
        fonts-dejavu fonts-liberation \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bench
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Pre-download OCR models so benchmark runs are offline and cold start only measures loading.
RUN python -c "import easyocr; easyocr.Reader(['en','es'], gpu=False, verbose=False)" \
    && python -c "from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType; RapidOCR(); RapidOCR(params={'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.MOBILE, 'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE, 'Rec.lang_type': LangRec.LATIN})"

WORKDIR /bench
CMD ["sh", "-c", "python generate_dataset.py && python run_benchmark.py"]
