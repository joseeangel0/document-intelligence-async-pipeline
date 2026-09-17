"""Central configuration. Every value can be overridden with an environment variable."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Infrastructure ---
    database_url: str = "postgresql+psycopg://docintel:docintel@postgres:5432/docintel"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "docintel"
    s3_secret_key: str = "docintel-secret"
    s3_region: str = "us-east-1"
    s3_bucket_documents: str = "documents"
    s3_bucket_results: str = "results"

    # --- Upload limits ---
    max_upload_mb: int = 50
    max_pdf_pages: int = 500
    max_image_pixels: int = 80_000_000  # ~ 9000x9000, protects against decompression bombs
    max_archive_uncompressed_mb: int = 300  # docx/pptx/xlsx are zip files (zip-bomb guard)
    max_filename_length: int = 255

    # --- Processing ---
    default_ocr_languages: str = "eng+spa"
    default_ocr_engine: str = "rapidocr"  # chosen by benchmark/ (see report §5)
    ocr_dpi: int = 300
    pdf_min_text_chars: int = 25  # below this a PDF page is considered "scanned" and OCR'd
    low_confidence_threshold: float = 60.0
    job_soft_time_limit_s: int = 900
    job_hard_time_limit_s: int = 960

    # --- Reliability ---
    max_attempts: int = 3
    heartbeat_interval_s: int = 5
    stale_heartbeat_s: int = 45  # PROCESSING job without heartbeat for this long => worker lost
    redispatch_after_s: int = 60  # QUEUED job not found in the broker for this long => re-dispatch
    queued_ttl_s: int = 24 * 3600  # a job can wait in the queue at most this long
    reaper_interval_s: int = 20
    retry_backoff_base_s: int = 10

    # --- Misc ---
    flower_url: str = "http://localhost:5555"
    minio_console_url: str = "http://localhost:9001"
    webhook_timeout_s: float = 5.0

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
