from pathlib import Path

import fitz

from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity


def verify_no_residual_pii(
    output_path: Path,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
) -> tuple[list[PiiEntity], dict[str, int]]:
    doc = fitz.open(output_path)

    try:
        residual_entities: list[PiiEntity] = []

        for page in doc:
            text = page.get_text("text", sort=True)
            residual_entities.extend(analyzer.analyze(text, mode=mode))

        return residual_entities, analyzer.count_by_type(residual_entities)

    finally:
        doc.close()