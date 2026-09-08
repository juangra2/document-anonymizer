class AnonymizerError(Exception):
    """Base exception for anonymizer errors."""


class FileValidationError(AnonymizerError):
    """Raised when the uploaded file is invalid."""


class UnsupportedDocumentError(AnonymizerError):
    """Raised when a document type is not supported by the current phase."""


class DocumentTooLargeError(FileValidationError):
    """Raised when the uploaded document exceeds configured size limits."""


class TooManyPagesError(UnsupportedDocumentError):
    """Raised when the PDF exceeds configured page limits."""


class EmptyPdfError(UnsupportedDocumentError):
    """Raised when the PDF has no pages."""


class EncryptedPdfError(UnsupportedDocumentError):
    """Raised when the PDF is encrypted or password-protected."""


class ScannedPdfNotSupportedError(UnsupportedDocumentError):
    """Raised when the PDF appears to be scanned and OCR is required."""


class ProcessingError(AnonymizerError):
    """Raised when processing fails unexpectedly."""


class RedactionVerificationError(AnonymizerError):
    """Raised when residual PII is detected after redaction."""

# Código estable por tipo de error, para que el cliente pueda reaccionar sin
# leer el mensaje.  Los mensajes de la API son técnicos y en inglés; la frase
# que ve el usuario la elige el frontend a partir de este código, que es lo que
# permite traducir la interfaz sin tocar el backend.
_ERROR_CODES: list[tuple[type, str]] = [
    (DocumentTooLargeError, "document_too_large"),
    (TooManyPagesError, "too_many_pages"),
    (EmptyPdfError, "empty_pdf"),
    (EncryptedPdfError, "encrypted_pdf"),
    (ScannedPdfNotSupportedError, "scanned_not_supported"),
    (FileValidationError, "invalid_file"),
    (UnsupportedDocumentError, "unsupported_document"),
    (RedactionVerificationError, "verification_failed"),
    (AnonymizerError, "processing_failed"),
]


def error_code_for(exc: BaseException) -> str:
    """Código estable del error, de la subclase más específica a la más general."""
    for error_type, code in _ERROR_CODES:
        if isinstance(exc, error_type):
            return code
    return "internal_error"
