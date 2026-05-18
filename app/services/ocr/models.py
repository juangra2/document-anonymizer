from dataclasses import dataclass

import fitz


@dataclass
class OcrToken:
    text: str
    confidence: float
    rect: fitz.Rect
    # Optional layout hint emitted by engines that provide pre-classification
    # (e.g. MinerU).  Values: "body", "header", "footer", "table", "form".
    # None means the token's zone will be inferred heuristically.
    zone_hint: str | None = None


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
