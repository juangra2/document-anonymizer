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

    ocr_engine: str = "easyocr"
    ocr_use_gpu: bool = True

    ocr_pdf_render_dpi: int = 200
    ocr_second_pass_verification: bool = True


settings = Settings()