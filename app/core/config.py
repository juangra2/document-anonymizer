from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "document-anonymizer"
    environment: str = "local"

    temp_root: str = "/tmp/anonymizer"

    max_upload_mb: int = 25
    max_pdf_pages: int = 50
    min_digital_text_chars: int = 30

    pdf_processing_mode: Literal["auto", "raster_ocr", "hybrid_secure"] = "hybrid_secure"

    store_original: bool = False
    store_redacted_temp: bool = True
    delete_temp_files: bool = True
    log_pii: bool = False

    result_ttl_minutes: int = 30

    presidio_score_threshold: float = 0.55
    default_redaction_mode: Literal["strict", "balanced"] = "strict"

    model_config = SettingsConfigDict(env_file=".env")

    # "auto" tries MinerU first and falls back to easyocr.
    # Explicit values: "mineru", "easyocr".
    ocr_engine: str = "auto"
    ocr_use_gpu: bool = True

    # Engine used when the primary (ocr_engine) is unavailable.
    ocr_fallback_engine: str = "easyocr"

    # MinerU pipeline backend: "pipeline_standard", "pipeline_fast", "vlm_openai".
    ocr_mineru_backend: str = "pipeline_standard"

    ocr_pdf_render_dpi: int = 200
    ocr_second_pass_verification: bool = True


settings = Settings()