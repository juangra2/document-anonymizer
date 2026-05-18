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