from dataclasses import dataclass


@dataclass(frozen=True)
class PiiEntity:
    entity_type: str
    start: int
    end: int
    score: float
    source: str = "presidio"
    text: str = ""