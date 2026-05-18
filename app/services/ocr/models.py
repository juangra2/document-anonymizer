from dataclasses import dataclass

import fitz


@dataclass
class OcrToken:
    text: str
    confidence: float
    rect: fitz.Rect


@dataclass
class OcrPage:
    page_index: int
    width: int
    height: int
    tokens: list[OcrToken]

    @property
    def text(self) -> str:
        return "\n".join(token.text for token in self.tokens)

    @property
    def avg_confidence(self) -> float | None:
        if not self.tokens:
            return None

        return sum(token.confidence for token in self.tokens) / len(self.tokens)