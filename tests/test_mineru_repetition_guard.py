"""MinerU degenerate-output guard (repetition / hallucination loop)."""
from types import SimpleNamespace

from app.services.ocr.mineru_engine import _blocks_to_tokens, _is_degenerate_content


# Real page-3 garbage: "α₁ 与 α₂ 与 …" repeated for hundreds of terms.
_LOOP = " ".join(r"\( \alpha_{%d} \) 与" % i for i in range(1, 60))

_NORMAL = (
    "Por el presente se le notifica que el documento con número de entrada "
    "1/2019/162.652,0 correspondiente a la sociedad TODINA INVESTMENTS SL "
    "fue presentado el día siete de noviembre de dos mil diecinueve."
)


def test_degenerate_detects_cjk_and_formula_loop():
    assert _is_degenerate_content(_LOOP)


def test_normal_spanish_text_not_degenerate():
    assert not _is_degenerate_content(_NORMAL)
    assert not _is_degenerate_content("IBAN ES18 0049 5114 9121 1615 8451")
    assert not _is_degenerate_content("")


def test_repeated_token_spam_detected():
    assert _is_degenerate_content(" ".join(["与"] * 50))


def test_blocks_to_tokens_drops_degenerate_block():
    blocks = [
        SimpleNamespace(content=_NORMAL, type="text", bbox=[0.1, 0.1, 0.9, 0.3]),
        SimpleNamespace(content=_LOOP, type="equation", bbox=[0.1, 0.4, 0.9, 0.9]),
    ]
    tokens = _blocks_to_tokens(blocks, page_width=1000, page_height=1400)
    texts = " ".join(t.text for t in tokens)
    assert "TODINA INVESTMENTS" in texts
    assert "\\alpha" not in texts and "与" not in texts
