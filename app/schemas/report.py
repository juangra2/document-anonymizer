from typing import Literal

from pydantic import BaseModel, Field


class DetectedEntityInfo(BaseModel):
    text: str
    type: str
    score: float
    source: str


class OcrInfo(BaseModel):
    enabled: bool = False
    engine: str | None = None
    pages_ocr_processed: int = 0
    images_processed: int = 0
    ocr_confidence_avg: float | None = None
    low_confidence_pages: list[int] = Field(default_factory=list)
    second_pass_verification: bool = False


class InputFileInfo(BaseModel):
    # El nombre del fichero original no figura aquí a propósito: el informe se
    # conserva horas y ese nombre suele llevar el del interesado.
    content_type: str | None = None
    size_bytes: int | None = None


class ProcessingLimits(BaseModel):
    max_upload_mb: int
    max_pdf_pages: int
    min_digital_text_chars: int


class SecurityChecks(BaseModel):
    physical_redaction_applied: bool = True
    post_redaction_verification_enabled: bool = True

    metadata_cleanup_attempted: bool = True
    xml_metadata_cleanup_attempted: bool = True
    annotations_cleanup_attempted: bool = True
    embedded_files_cleanup_attempted: bool = True
    attached_files_cleanup_attempted: bool = True
    form_fields_cleanup_attempted: bool = True
    hidden_text_cleanup_attempted: bool = True
    javascript_cleanup_attempted: bool = True
    links_cleanup_attempted: bool = True

    report_contains_pii_values: bool = False


class VerificationSummary(BaseModel):
    """Compact summary of post-redaction verification results included in the report."""

    overall_risk_score: float
    risk_level: Literal["none", "low", "medium", "high"]
    layers_run: list[str] = Field(default_factory=list)
    critical_pages: list[int] = Field(default_factory=list)
    entities_by_layer: dict[str, int] = Field(default_factory=dict)
    total_residual_entities: int = 0
    passed: bool = True


class ProcessingReport(BaseModel):
    job_id: str

    status: Literal[
        "completed",
        "completed_with_warnings",
        "failed",
        "manual_review_required",
    ]

    document_type: Literal[
        "pdf_digital",
        "pdf_scanned",
        "pdf_hybrid",
        "pdf_scanned_or_image_only",
        "pdf_rasterized_ocr",
        "image",
        "pdf_empty",
        "unsupported",
    ] = "pdf_digital"

    language_detected: list[str] = Field(default_factory=lambda: ["es"])

    input: InputFileInfo = Field(default_factory=InputFileInfo)

    limits: ProcessingLimits | None = None

    pages: int = 0
    selectable_text_chars: int = 0

    entities_detected: int = 0
    entities_redacted: int = 0
    residual_entities: int = 0

    entities_by_type: dict[str, int] = Field(default_factory=dict)
    # Redacciones estructurales (valor tras una etiqueta de formulario, columna
    # sensible de tabla) que no se atribuyen a ningún tipo del catálogo.  Van
    # aparte de entities_by_type para que ese resumen cuadre con
    # entity_types_applied / entity_types_disabled: antes se contaban bajo una
    # clave LAYOUT_VALUE que no era ningún tipo.
    structural_redactions_untyped: int = 0
    residual_entities_by_type: dict[str, int] = Field(default_factory=dict)

    manual_review_required: bool = False
    warnings: list[str] = Field(default_factory=list)

    redaction_strategy: str = "physical_pdf_redaction"
    redaction_mode: Literal["strict"] = "strict"

    # Bloque C — configuración de tipos aplicada en esta petición.  Permite al
    # usuario ver el efecto de su selección: qué se buscó y qué se dejó fuera.
    entity_types_applied: list[str] = Field(default_factory=list)
    entity_types_disabled: list[str] = Field(default_factory=list)
    output_format: Literal["pdf", "zip"] = "pdf"

    security_checks: SecurityChecks = Field(default_factory=SecurityChecks)

    processing_ms: int | None = None

    ocr: OcrInfo = Field(default_factory=OcrInfo)

    # Populated after post-redaction verification runs.
    verification: VerificationSummary | None = None

    # Populated only when ?include_entities=true is requested (evaluation / audit use).
    detected_entities: list[DetectedEntityInfo] = Field(default_factory=list)