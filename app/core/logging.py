import logging
import re


PII_PATTERNS = [
    re.compile(r"\b\d{8}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[A-HJNPQRSUVW]\d{7}[0-9A-J]\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+34[\s\-]?)?(?:6|7|8|9)\d{2}[\s\-]?\d{3}[\s\-]?\d{3}(?!\d)"),
    re.compile(r"\bES\d{2}(?:[\s\-]?\d{4}){5}\b", re.IGNORECASE),
]


class NoPIIFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        for pattern in PII_PATTERNS:
            message = pattern.sub("[REDACTED]", message)

        record.msg = message
        record.args = ()

        return True


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    root_logger = logging.getLogger()
    root_logger.addFilter(NoPIIFilter())