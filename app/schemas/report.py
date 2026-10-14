from typing import Literal

from pydantic import BaseModel, Field


class DetectedEntityInfo(BaseModel):
    text: str
    type: str
    score: float
    source: str


# Codigos estables de los avisos con codigo (`ProcessingReport.coded_warnings`).
PLAN_TITLEBLOCK_ONLY_VERIFIED = "plan_titleblock_only_verified"
PAGES_NOT_VERIFIED = "pages_not_verified"
SAFETY_NET_ACTED = "safety_net_acted"
SAFETY_NET_STILL_READABLE = "safety_net_still_readable"
#: Todos: cada uno necesita su frase en la pantalla (lo comprueba test_web.py).
CODED_WARNING_CODES = (PLAN_TITLEBLOCK_ONLY_VERIFIED, PAGES_NOT_VERIFIED,
                       SAFETY_NET_ACTED, SAFETY_NET_STILL_READABLE)


class CodedWarning(BaseModel):
    """Aviso del informe con codigo estable: el frontend lo traduce sin depender
    de la frase en ingles de `warnings`, que se mantiene para quien lee el JSON."""

    code: str = Field(
        description="`plan_titleblock_only_verified` (en las paginas de plano solo se "
                    "verifico el cajetin), `pages_not_verified` (paginas que la "
                    "verificacion final no pudo comprobar; requiere revision manual), "
                    "`safety_net_acted` (la comprobacion tras tachar aun leia datos y "
                    "se taparon) o `safety_net_still_readable` (datos que siguen "
                    "legibles tras la red; requiere revision manual).",
        examples=[PLAN_TITLEBLOCK_ONLY_VERIFIED],
    )
    pages: list[int] = Field(default_factory=list,
                             description="Numeracion de usuario: la primera es la 1.")
    types: list[str] = Field(default_factory=list,
                             description="Tipos de dato implicados, si el aviso los tiene.")
    counts: dict[str, int] = Field(default_factory=dict,
                                   description="Recuentos del aviso (p. ej. `images`, `zones`).")


class PageRedactedInFull(BaseModel):
    """Página tachada entera porque el OCR no pudo leerla.

    No incluye las que se tachan por decisión de contenido (copia de DNI,
    autoliquidación): esas son intencionadas y van en sus propios avisos.
    """

    page: int = Field(description="Numeración de usuario: la primera es la 1.")
    reason: str = Field(
        description="`ocr_unreadable` (ilegible en todos los giros) o "
                    "`ocr_timeout` (agotó el presupuesto de la página).",
        examples=["ocr_timeout"],
    )


class OcrInfo(BaseModel):
    enabled: bool = False
    engine: str | None = None
    pages_ocr_processed: int = 0
    images_processed: int = 0
    ocr_confidence_avg: float | None = None
    low_confidence_pages: list[int] = Field(default_factory=list)
    second_pass_verification: bool = False
    # Paginas con texto e imagen que el criterio de superficie decidio NO mirar
    # (ver ocr_imagen_pagina).  No es un aviso al usuario: es el numero donde se
    # veria un dia que la imagen relevante era pequena, antes que en una fuga.
    pages_with_image_not_ocr: int = 0
    # De ellas, las de la zona gris: alguna imagen por encima del minimo por
    # imagen pero sin llegar al umbral de la pagina.  Un DNI pegado a mitad de
    # escala (1,9 %) caeria aqui.
    pages_image_grey_zone: int = 0
    # Red de seguridad: lo que la segunda pasada todavia leia tras tachar y se
    # tapo antes de entregar.  Si estos numeros suben, la primera pasada esta
    # dejando cosas a la vista y conviene saber donde.
    safety_net_images_redacted: int = 0
    safety_net_zones_repainted: int = 0
    safety_net_still_readable: int = 0


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
    # Documento parado a medias: `pages` son las páginas ENTREGADAS y
    # `pages_total_original` las que tenía el documento subido.
    partial: bool = False
    pages_total_original: int | None = None
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
    coded_warnings: list[CodedWarning] = Field(
        default_factory=list,
        description="Avisos con codigo estable, ademas de su frase en `warnings`.",
    )
    pages_redacted_in_full: list[PageRedactedInFull] = Field(
        default_factory=list,
        description="Páginas tachadas enteras porque el OCR no pudo leerlas, "
                    "con el motivo de cada una.",
    )

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