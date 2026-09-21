"""Turning elements into retrievable chunks.

Three decisions define this, and each is a departure from the usual recipe of
splitting a document's plain text every 500 tokens with a 50 token overlap.

**Chunks never cross a section boundary.** A chunk that ends in Methods and
begins in Results answers neither question well, and a citation into it points
at two places at once. Elements already carry their section, so respecting it
costs nothing.

**A table or a figure is one chunk, never split.** Splitting a results table in
half is how a system ends up able to find the column headers and the numbers
but never both together. Tables are indexed whole with their caption, and a
figure is indexed as its caption plus whatever OCR read out of it.

**Every chunk carries its own context, computed rather than generated.**
Anthropic's contextual retrieval prepends an LLM written sentence to each chunk
and reports a large reduction in retrieval failures, at the cost of one model
call per chunk. Most of what that sentence supplies is the chunk's position in
the document, which the parser already knows exactly: the paper's title, the
heading trail, the figure label. Prepending those is free, deterministic, and
works with no key configured at all. The LLM written version remains available
as an opt in for the cases where it genuinely adds something.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import settings
from ..parse.base import Element, Kind, Section
from ..util import truncate

# Roughly four characters per token for English prose. Exact tokenisation would
# mean loading the encoder's tokeniser here, which would tie chunking to
# whichever embedding backend happens to be selected and change every stored
# chunk when that changes. The approximation is stable, which matters more.
_CHARS_PER_TOKEN = 4

# Sentence boundary that does not fire on "Fig. 3", "et al.", "e.g." or a
# decimal point, all of which are dense in a paper and all of which the naive
# split-on-period recipe gets wrong.
_SENTENCE_END = re.compile(
    r"(?<![A-Z][a-z]\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bcf\.)(?<!\bvs\.)"
    r"(?<!\bFig\.)(?<!\bEq\.)(?<!\bTab\.)(?<!\bSec\.)(?<!\bRef\.)(?<!\bal\.)"
    r"(?<!\bNo\.)(?<!\bpp\.)(?<!\bvol\.)(?<!\d\.\d)"
    r"(?<=[.!?])[\s ]+(?=[A-Z“(\[])"
)


@dataclass
class Chunk:
    """One retrievable unit, and the thing a citation points at."""

    text: str                      # what is embedded and searched
    display_text: str = ""         # what is shown, without the context prefix
    kind: str = "text"             # text, table, figure, formula, reference
    page: int = 0
    section: str = Section.UNKNOWN.value
    section_path: list[str] = field(default_factory=list)
    label: str = ""
    caption: str = ""
    # Element ids this chunk was built from, so a hit can be traced back to a
    # bounding box on a page.
    element_ids: list[int] = field(default_factory=list)
    order: int = 0
    token_estimate: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "display_text": self.display_text or self.text,
            "kind": self.kind,
            "page": self.page,
            "section": self.section,
            "section_path": self.section_path,
            "label": self.label,
            "caption": self.caption,
            "element_ids": self.element_ids,
            "order": self.order,
            "token_estimate": self.token_estimate,
            "extra": self.extra,
        }


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


# Tokens that only ever appear in a model's vocabulary dump, never in prose.
_VOCAB_NOISE = re.compile(r"<(pad|eos|bos|unk|s|/s|mask|sep|cls)>", re.IGNORECASE)


def looks_degenerate(text: str) -> bool:
    """Whether a run of text is extraction noise rather than writing.

    Papers embed text inside figures, and a PDF's text layer hands it over
    looking exactly like prose. The attention visualisations in the Transformer
    paper are the clearest case: pages of word soup like ". , , - is in its be
    be we my will but are this just The Law what never <pad>", which a text
    extractor reports as ordinary paragraphs. Indexed, they rank highly for
    almost any query, because they contain almost every common word, and they
    answer nothing.

    Three signals, any one of which is enough. Each was chosen by looking at
    what the real false positives had in common rather than at what would be
    elegant:

    * A vocabulary token such as `<pad>`, which no author types.
    * A third or more of tokens being a single character, which is punctuation
      and axis labels rather than sentences.
    * A type to token ratio below a fifth on a long run, which is the
      signature of a heat map's repeated row and column labels.
    """
    stripped = (text or "").strip()
    if len(stripped) < 40:
        return False
    if _VOCAB_NOISE.search(stripped):
        return True

    tokens = stripped.split()
    if len(tokens) < 12:
        return False

    singles = sum(1 for t in tokens if len(t) == 1)
    if singles / len(tokens) > 0.33:
        return True

    if len(tokens) >= 40:
        unique = len({t.lower() for t in tokens})
        if unique / len(tokens) < 0.2:
            return True

    # Prose has sentences. A run this long with no sentence ending at all is a
    # list of labels, not writing.
    if len(tokens) >= 60 and not re.search(r"[.!?]\s", stripped):
        return True
    return False


def _context_prefix(paper_title: str, element: Element) -> str:
    """The deterministic half of contextual retrieval.

    Short on purpose. The prefix is embedded along with the chunk, so a long
    one dilutes the chunk's own meaning, which is the failure mode that makes
    naive header stuffing perform worse than no context at all.
    """
    parts: list[str] = []
    if paper_title:
        parts.append(truncate(paper_title, 90, suffix=""))
    trail = [h for h in element.section_path if h.strip()]
    if trail:
        parts.append(" > ".join(truncate(h, 60, suffix="") for h in trail[-2:]))
    elif element.section is not Section.UNKNOWN:
        parts.append(element.section.value.replace("_", " "))
    return " | ".join(parts)


def chunk_elements(
    elements: list[Element],
    *,
    paper_title: str = "",
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
    min_chars: int | None = None,
) -> list[Chunk]:
    """Build chunks from a parsed paper's elements, in reading order."""
    max_tokens = max_tokens or settings.CHUNK_TOKENS
    overlap_tokens = overlap_tokens if overlap_tokens is not None else settings.CHUNK_OVERLAP
    min_chars = min_chars or settings.MIN_CHUNK_CHARS

    chunks: list[Chunk] = []
    # Prose accumulates until the budget is full or the section changes.
    buffer: list[Element] = []
    buffer_chars = 0
    budget_chars = max_tokens * _CHARS_PER_TOKEN

    def flush() -> None:
        nonlocal buffer, buffer_chars
        if not buffer:
            return
        chunks.extend(_prose_chunks(buffer, paper_title, budget_chars,
                                    overlap_tokens * _CHARS_PER_TOKEN, min_chars))
        buffer = []
        buffer_chars = 0

    for element in sorted(elements, key=lambda e: e.order):
        if element.kind is Kind.TABLE:
            flush()
            chunk = _table_chunk(element, paper_title)
            if chunk:
                chunks.append(chunk)
            continue
        if element.kind is Kind.FIGURE:
            flush()
            chunk = _figure_chunk(element, paper_title)
            if chunk:
                chunks.append(chunk)
            continue
        if element.kind in (Kind.FORMULA, Kind.CODE):
            flush()
            chunk = _atomic_chunk(element, paper_title)
            if chunk:
                chunks.append(chunk)
            continue
        if element.kind is Kind.REFERENCE:
            # Every reference entry is its own chunk. Merging them produces a
            # chunk that matches any citation query and answers none of them.
            chunk = _atomic_chunk(element, paper_title, kind="reference")
            if chunk:
                flush()
                chunks.append(chunk)
            continue
        # Running heads and page numbers are stored and searchable as elements
        # but must never enter a prose chunk.
        if element.kind in (Kind.PAGE_HEADER, Kind.PAGE_FOOTER, Kind.OTHER):
            continue
        if element.kind is Kind.CAPTION and element.linked_id:
            # Already carried by the figure or table it labels.
            continue
        if not element.text.strip():
            continue

        # A heading starts a new chunk, and a section change forces one even
        # when the budget is nowhere near full.
        if buffer:
            previous = buffer[-1]
            if element.section is not previous.section or element.kind is Kind.HEADING:
                flush()
            elif buffer_chars + len(element.text) > budget_chars:
                flush()
        buffer.append(element)
        buffer_chars += len(element.text) + 1

    flush()
    for position, chunk in enumerate(chunks):
        chunk.order = position
    return chunks


def _prose_chunks(
    elements: list[Element],
    paper_title: str,
    budget_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> list[Chunk]:
    """Split a run of same-section prose into budgeted, sentence aligned chunks."""
    lead = elements[0]
    body = "\n".join(e.text for e in elements if e.text.strip()).strip()
    if len(body) < min_chars:
        # Too short to stand alone. A lone heading or a stray line is dropped
        # rather than indexed, because it matches broadly and answers nothing.
        return []

    if looks_degenerate(body):
        # The text is still stored as an element and still findable by the
        # exhaustive search in the All Views screens. It is only kept out of
        # the retrieval index, where it would crowd out real answers.
        return []

    prefix = _context_prefix(paper_title, lead)
    pieces = _split_to_budget(body, budget_chars, overlap_chars)
    out: list[Chunk] = []
    for piece in pieces:
        if len(piece) < min_chars and len(pieces) > 1:
            continue
        if looks_degenerate(piece):
            continue
        out.append(Chunk(
            text=f"[{prefix}]\n{piece}" if prefix else piece,
            display_text=piece,
            kind="text",
            page=lead.page,
            section=lead.section.value,
            section_path=list(lead.section_path),
            element_ids=[e.order for e in elements],
            token_estimate=estimate_tokens(piece),
            extra={"pages": sorted({e.page for e in elements})},
        ))
    return out


def _split_to_budget(text: str, budget: int, overlap: int) -> list[str]:
    """Split on sentence boundaries, packing up to the budget.

    Overlap is taken as whole trailing sentences rather than a fixed character
    count, so a chunk never begins mid sentence. A chunk that starts halfway
    through a clause embeds badly and reads badly when shown as a citation.
    """
    if len(text) <= budget:
        return [text]

    sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
    if not sentences:
        return [text[i:i + budget] for i in range(0, len(text), max(1, budget - overlap))]

    out: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in sentences:
        if current and size + len(sentence) > budget:
            out.append(" ".join(current).strip())
            carry: list[str] = []
            carried = 0
            for previous in reversed(current):
                if carried + len(previous) > overlap:
                    break
                carry.insert(0, previous)
                carried += len(previous) + 1
            current = carry
            size = carried
        # A single sentence longer than the whole budget, which happens with a
        # badly extracted table caption, is cut on whitespace rather than
        # dropped or allowed to blow the budget.
        if len(sentence) > budget:
            if current:
                out.append(" ".join(current).strip())
                current, size = [], 0
            for start in range(0, len(sentence), budget):
                out.append(sentence[start:start + budget].strip())
            continue
        current.append(sentence)
        size += len(sentence) + 1
    if current:
        out.append(" ".join(current).strip())
    return [piece for piece in out if piece.strip()]


def _table_chunk(element: Element, paper_title: str) -> Chunk | None:
    """A whole table, as markdown, with its caption.

    Markdown rather than the grid or the HTML: a model reads a markdown table
    far more reliably than either, and the cells are still searchable because
    the lexical index reads `Element.search_text()` separately.
    """
    table = element.table
    if table is None:
        return None
    body = table.markdown or table.cell_text()
    if not body.strip() and not element.caption:
        return None

    prefix = _context_prefix(paper_title, element)
    label = element.label or "Table"
    header = f"{label}. {element.caption}".strip().rstrip(".")
    confident = bool(element.extra.get("structure_confident", True))
    note = "" if confident else (
        "\n(The column structure of this table was inferred and may be wrong. "
        "The rendered image of it is authoritative.)"
    )
    text = "\n".join(p for p in (f"[{prefix}]" if prefix else "", header, body, note) if p)
    return Chunk(
        text=text,
        display_text=f"{header}\n{body}".strip(),
        kind="table",
        page=element.page,
        section=element.section.value,
        section_path=list(element.section_path),
        label=label,
        caption=element.caption,
        element_ids=[element.order],
        token_estimate=estimate_tokens(text),
        extra={
            "num_rows": table.num_rows,
            "num_cols": table.num_cols,
            "structure_confident": confident,
            "image_digest": element.image.digest if element.image else "",
        },
    )


def _figure_chunk(element: Element, paper_title: str) -> Chunk | None:
    """A figure, as its caption plus whatever text was read out of it.

    A figure with no caption and no OCR text still becomes a chunk, because the
    visual index can retrieve it and the reader needs it listed. It carries its
    label and page so it is findable by "figure 3" even with nothing else.
    """
    image = element.image
    prefix = _context_prefix(paper_title, element)
    label = element.label or f"Figure on page {element.page}"
    parts = [f"[{prefix}]" if prefix else "", label]
    if element.caption:
        parts.append(element.caption)
    if image and image.ocr_text.strip():
        parts.append(f"Text in the figure: {image.ocr_text.strip()}")
    if image and image.description.strip():
        parts.append(image.description.strip())
    text = "\n".join(p for p in parts if p)

    return Chunk(
        text=text,
        display_text="\n".join(p for p in parts[1:] if p),
        kind="figure",
        page=element.page,
        section=element.section.value,
        section_path=list(element.section_path),
        label=label,
        caption=element.caption,
        element_ids=[element.order],
        token_estimate=estimate_tokens(text),
        extra={
            "image_digest": image.digest if image else "",
            "width": image.width if image else 0,
            "height": image.height if image else 0,
            "has_ocr": bool(image and image.ocr_text.strip()),
        },
    )


def _atomic_chunk(element: Element, paper_title: str, *, kind: str = "") -> Chunk | None:
    text = element.text.strip()
    if not text:
        return None
    prefix = _context_prefix(paper_title, element)
    return Chunk(
        text=f"[{prefix}]\n{text}" if prefix else text,
        display_text=text,
        kind=kind or element.kind.value,
        page=element.page,
        section=element.section.value,
        section_path=list(element.section_path),
        label=element.label,
        element_ids=[element.order],
        token_estimate=estimate_tokens(text),
    )
