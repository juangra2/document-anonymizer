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

    # NER backend for PERSON/LOCATION/ORGANIZATION detection.
    # "spacy"        → es_core_news_md (default; fast, no download).
    # "transformers" → a HuggingFace token-classification model wrapped by
    #                  Presidio's TransformersNlpEngine.  Falls back to spaCy if
    #                  the transformer stack is unavailable.  Model loads lazily.
    # Default is "spacy": BETO (mrm8488) was evaluated and regressed PERSON F1
    # (0.80 vs 0.82) and mishandles Valencian/Catalan (span-alignment failures on
    # D'UNA PART:, D.N.I.:).  Engine kept configurable for future opt-in.
    # INE public gazetteers (surnames/given names + municipalities/provinces).
    # gazetteer_address: compositional ADDRESS extractor with CP↔province↔
    #   municipality validation.  gazetteer_person: reinforce low-confidence
    #   PERSON NER on name/surname matches and drop toponym false positives.
    gazetteer_address: bool = True
    gazetteer_person: bool = True

    # Redact the administrative case-file number (e.g. E-03001-2022-000074-00).
    # Off by default: the expediente is a public administrative reference.  The
    # output-register / "número de salida" is always redacted (it identifies the
    # specific communication addressed to a citizen).
    redact_case_file_number: bool = False

    # Detect and redact handwritten signatures (embedded images / ink) in the
    # digital PDF path, anchored to a signature cue ("Fdo.", "Firma", …).
    detect_visual_signatures: bool = True

    ner_engine: Literal["transformers", "spacy"] = "spacy"
    ner_transformers_model: str = "mrm8488/bert-spanish-cased-finetuned-ner"
    ner_spacy_model: str = "es_core_news_md"
    ner_use_gpu: bool = True

    model_config = SettingsConfigDict(env_file=".env")

    # "auto" tries MinerU first and falls back to easyocr.
    # Explicit values: "mineru", "easyocr".
    ocr_engine: str = "auto"
    ocr_use_gpu: bool = True

    # Engine used when the primary (ocr_engine) is unavailable.
    ocr_fallback_engine: str = "easyocr"

    # MinerU backend passed to MinerUClient(backend=...).
    # "transformers" for MinerU2.5-Pro via mineru-vl-utils.
    ocr_mineru_backend: str = "transformers"

    # Local path to the downloaded MinerU2.5-Pro model weights.
    # Download: hf download opendatalab/MinerU2.5-Pro-2604-1.2B \
    #               --local-dir model/MinerU2.5-Pro-2604-1.2B
    ocr_mineru_model_path: str = "model/MinerU2.5-Pro-2604-1.2B"

    ocr_pdf_render_dpi: int = 200
    ocr_second_pass_verification: bool = True

    # Per-page OCR budget (seconds).  A page that exceeds it (e.g. a very large
    # blueprint that stalls the OCR engine) is skipped, logged, and flagged for
    # manual review instead of hanging the whole job.
    ocr_page_timeout_s: int = 180

    # "Image-dominant" page fast path: a page whose largest embedded image covers
    # at least this fraction of the page AND exceeds this pixel count is treated
    # as a plan/photo — heavy text OCR is skipped (only visual codes/signatures
    # are redacted), avoiding multi-minute stalls.  The megapixel floor is set
    # well above a normal A4 scan (~9 MP at 300 DPI) so ordinary scanned text
    # pages still get full OCR.  The megapixel floor (25 MP) clears a 600-DPI A4
    # scan, so only oversized plans (A1/A0, tens of MP) take the fast path; the
    # per-page timeout is the safety net for everything in between.
    ocr_image_dominant_area_frac: float = 0.70
    ocr_image_dominant_megapixels: float = 25.0

    # Post-redaction verification thresholds [0.0–1.0].
    # review  : risk >= threshold → manual_review_required = True.
    # reject  : risk >= threshold → RedactionVerificationError raised.
    verify_risk_threshold_review: float = 0.30
    verify_risk_threshold_reject: float = 0.70
    # Comma-separated layer names active per redaction mode.
    verify_layers_strict:   str = "textual,metadata,binary"
    verify_layers_balanced: str = "textual,metadata"
    # OCR verification layer is opt-in — it doubles processing time.
    verify_ocr_layer_enabled: bool = False


settings = Settings()