# One image for api, workers, scheduler and flower (different commands).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # One OCR thread per task: parallelism comes from Celery worker processes, not from oversubscribed threads.
    OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1 \
    OCR_THREADS=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-osd \
        libmagic1 libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 # Bake OCR models into the image: workers start offline and cold start only loads them.
 && python -c "from rapidocr import RapidOCR; RapidOCR(params={'Global.use_cls': False})"

RUN useradd --create-home --uid 10001 docintel
COPY app ./app
COPY tests ./tests
COPY pytest.ini .
COPY samples ./samples
USER docintel

EXPOSE 8000
CMD ["uvicorn", "app.api.main:asgi", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
