"""Finding display equations in a PDF, without a formula model.

The obvious test, "does this line use a maths font", does not work. In a LaTeX
paper the body font and the maths fonts are mixed constantly inside ordinary
prose, because every inline symbol is set in one:

    fonts=['CMMI10', 'CMMI7', 'NimbusRomNo9L-Regu']
    'queries and keys of dimension dk, and values of dimension dv. We compute'

That is a sentence, not an equation. What separates a display equation is that
it contains *no body text font at all*:

    fonts=['CMMI10', 'CMMI7', 'CMR10']
    'Attention(Q, K, V ) = softmax(QKT'

Both lines are from page 4 of "Attention Is All You Need". The first is prose
with symbols in it, the second is the scaled dot product attention equation.
The absence of the body font is the discriminator, and it is nearly free to
compute because the font of every span is already being collected for heading
detection.

Two supporting signals refine it: a display equation is set away from the left
margin and does not run to the right margin, and it often carries a right
aligned number like "(1)". Neither is required, because plenty of papers number
nothing, but both raise confidence and the number is worth capturing as a label.

What this does not do is convert the equation to LaTeX. Recovering `\\frac{QK^T}
{\\sqrt{d_k}}` from glyph positions is a real research problem, and Docling's
formula enrichment in the deep tier is the right tool. What this produces is
the equation's text, its bounding box and its number, which is enough to find
it, cite it, show it on the page and hand the region to a vision model.
"""
from __future__ import annotations

import re
from typing import Any

# Font families that only ever carry mathematics. Computer Modern's maths
# faces cover LaTeX, the others cover the common alternatives.
_MATH_FONT = re.compile(
    r"CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|EUSM|EUFM|EURM|RSFS|WASY|STMARY|"
    r"STIXMath|XITSMath|LMMath|LMRoman.*Math|MathJax|NimbusMath|"
    r"TeXGyre.*Math|Latin.*Math|Cambria Math|Asana|Neo Euler",
    re.IGNORECASE,
)

# A trailing equation number: "(1)", "(3.2)", "(A.4)".
_EQUATION_NUMBER = re.compile(r"\(\s*([0-9]+(?:\.[0-9]+)*|[A-Z]\.?[0-9]+)\s*\)\s*$")

# A relation is what makes a line an equation rather than a run of symbols.
# Almost every display equation states that something equals, is bounded by, or
# maps to something else.
_RELATIONS = set("=≈≠≡≤≥≪≫∈∉⊂⊃∼≅∝→↦⇒⇔<>")

# `<pad>`, `<EOS>`, `</>`. Angle brackets are in the relation set because a real
# equation uses them, and a model's vocabulary token uses them too. A paper that
# visualises attention prints pages of these, and they were being read as
# equations because they carry a relation and no body font.
_TOKEN_TAG = re.compile(r"</?[A-Za-z][\w/]*>|</>")

# Operators and Greek, used only to measure how mathematical a line is. These
# deliberately exclude the asterisk: U+2217 is a genuine maths operator and is
# also what every paper uses for an author footnote marker, so counting it as
# evidence turns the author list into a page of equations. That was the first
# thing this detector got wrong.
_MATH_GLYPHS = set(
    "+×÷±∓∪∩∀∃∇∂∫∑∏√∞⊕⊗·⌊⌋⌈⌉"
    "αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
) | _RELATIONS

# Below this share of characters set in a maths face, a line needs a relation to
# count as an equation. Above it, the line is mathematical enough on its own.
_MATH_FONT_RATIO = 0.40

# A display equation is set in from the margins. Measured as a fraction of the
# body's own width, so it does not depend on the page size.
_MAX_WIDTH_FRACTION = 0.92
# and is at least this wide, which rejects a stray superscript on its own line.
_MIN_CHARS = 4


def _font_weights(lines: list[dict[str, Any]]) -> dict[str, int]:
    """Characters set in each font across the document."""
    weights: dict[str, int] = {}
    for line in lines:
        for font, count in (line.get("font_chars") or {}).items():
            weights[font] = weights.get(font, 0) + count
    return weights


def body_fonts(lines: list[dict[str, Any]]) -> frozenset[str]:
    """The fonts the document's prose is set in.

    Everything down to 85 percent of the characters, so a paper that mixes a
    roman and a bold face for body text treats both as body. A maths face never
    reaches this share, because symbols are a small fraction of the characters
    even in a heavily mathematical paper.
    """
    weights = _font_weights(lines)
    if not weights:
        return frozenset()
    total = sum(weights.values())
    ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    chosen: set[str] = set()
    running = 0
    for font, count in ranked:
        if _MATH_FONT.search(font):
            continue
        chosen.add(font)
        running += count
        if running >= total * 0.85:
            break
    return frozenset(chosen)


def is_display_formula(
    line: dict[str, Any], body: frozenset[str], body_width: float
) -> bool:
    """Whether one line is a display equation rather than prose."""
    text = (line.get("text") or "").strip()
    if len(text) < _MIN_CHARS:
        return False
    # A line ending in a full stop is a sentence. Equations do sometimes end
    # with punctuation, but a lone period after a long roman run is prose.
    if _TOKEN_TAG.search(text):
        return False
    fonts = set(line.get("font_chars") or {})
    if not fonts:
        return False

    # The discriminator: no body font anywhere in the line.
    if fonts & body:
        return False

    font_chars = line.get("font_chars") or {}
    total_chars = sum(font_chars.values()) or 1
    math_chars = sum(n for f, n in font_chars.items() if _MATH_FONT.search(f))
    math_ratio = math_chars / total_chars

    has_relation = any(ch in _RELATIONS for ch in text)
    has_math_glyph = any(ch in _MATH_GLYPHS for ch in text)

    # An author line on a title page is set in a non body font and carries a
    # footnote asterisk from a maths face, which passes every weaker test. It
    # states nothing and is mostly letters, so it fails both of these.
    if not has_relation and math_ratio < _MATH_FONT_RATIO:
        return False
    if not (has_math_glyph or math_ratio >= _MATH_FONT_RATIO):
        return False

    # Mostly letters and spaces with no relation is a name, a heading or a
    # caption fragment, whatever font it happens to be set in.
    letters = sum(1 for ch in text if ch.isalpha() or ch.isspace())
    if not has_relation and letters / max(1, len(text)) > 0.85:
        return False

    bbox = line.get("bbox")
    if bbox is not None and body_width > 0:
        # Prose runs to the right margin; a display equation does not.
        if bbox.width > body_width * _MAX_WIDTH_FRACTION:
            return False

    return True


def equation_number(text: str) -> str:
    match = _EQUATION_NUMBER.search(text or "")
    return match.group(1) if match else ""


def strip_number(text: str) -> str:
    return _EQUATION_NUMBER.sub("", text or "").strip()
