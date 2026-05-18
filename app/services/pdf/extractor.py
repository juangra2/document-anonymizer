from dataclasses import dataclass

import fitz


@dataclass
class WordBox:
    text: str
    start: int
    end: int
    rect: fitz.Rect
    block_no: int
    line_no: int
    word_no: int


@dataclass
class PageText:
    page_index: int
    text: str
    words: list[WordBox]


def extract_page_text(page: fitz.Page, page_index: int) -> PageText:
    raw_words = page.get_text("words", sort=True)

    grouped: dict[tuple[int, int], list[tuple]] = {}

    for item in raw_words:
        x0, y0, x1, y1, word, block_no, line_no, word_no = item[:8]

        if not str(word).strip():
            continue

        grouped.setdefault((block_no, line_no), []).append(item)

    words: list[WordBox] = []
    parts: list[str] = []
    cursor = 0

    ordered_lines = sorted(grouped.items(), key=lambda item: item[0])

    for line_index, ((block_no, line_no), line_words) in enumerate(ordered_lines):
        if line_index > 0:
            parts.append("\n")
            cursor += 1

        line_words = sorted(line_words, key=lambda item: (item[0], item[7]))

        for word_index, item in enumerate(line_words):
            x0, y0, x1, y1, word, block_no, line_no, word_no = item[:8]

            if word_index > 0:
                parts.append(" ")
                cursor += 1

            start = cursor
            parts.append(word)
            cursor += len(word)
            end = cursor

            words.append(
                WordBox(
                    text=word,
                    start=start,
                    end=end,
                    rect=fitz.Rect(x0, y0, x1, y1),
                    block_no=block_no,
                    line_no=line_no,
                    word_no=word_no,
                )
            )

    return PageText(
        page_index=page_index,
        text="".join(parts),
        words=words,
    )


def rects_for_span(
    page_text: PageText,
    start: int,
    end: int,
    padding: float = 0.5,
) -> list[fitz.Rect]:
    matched_words = [
        word for word in page_text.words
        if word.end > start and word.start < end
    ]

    if not matched_words:
        return []

    grouped: dict[tuple[int, int], list[WordBox]] = {}

    for word in matched_words:
        grouped.setdefault((word.block_no, word.line_no), []).append(word)

    rects: list[fitz.Rect] = []

    for line_words in grouped.values():
        rect = fitz.Rect(line_words[0].rect)

        for word in line_words[1:]:
            rect |= word.rect

        rect.x0 -= padding
        rect.y0 -= padding
        rect.x1 += padding
        rect.y1 += padding

        rects.append(rect)

    return rects