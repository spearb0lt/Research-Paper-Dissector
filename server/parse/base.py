"""What a parsed paper is, independent of which parser produced it.

Every parser in this package returns the same `ParseResult`, so the indexing,
retrieval and browsing layers never learn which one ran. That is what makes the
fast and deep tiers interchangeable, and what lets a paper parsed on a laptop
be served from a serverless deployment that could never have parsed it.

The central idea is that an `Element` is the unit of everything. It is what is
extracted, what is stored, what is retrieved, what is cited, and what the All
Views screens list. A citation therefore always points at a real region of a
real page rather than at a chunk of text that has lost its origin, which is the
failure mode of a text only pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Kind(str, Enum):
    """What an element is. Ordered roughly by how a reader encounters it."""

    TITLE = "title"
    AUTHORS = "authors"
    ABSTRACT = "abstract"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    FORMULA = "formula"
    CODE = "code"
    FOOTNOTE = "footnote"
    REFERENCE = "reference"
    FORM_FIELD = "form_field"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    OTHER = "other"

    @property
    def is_visual(self) -> bool:
        return self in (Kind.FIGURE, Kind.TABLE)

    @property
    def is_prose(self) -> bool:
        """Whether this element's text belongs in the narrative reading order.

        Running heads, footers and reference entries are all real text that
        must be searchable, but folding them into the narrative would splice a
        page number into the middle of a sentence.
        """
        return self in (
            Kind.TITLE, Kind.ABSTRACT, Kind.HEADING, Kind.PARAGRAPH,
            Kind.LIST_ITEM, Kind.CAPTION, Kind.FOOTNOTE, Kind.CODE,
        )


# The IMRaD sections a reader actually asks about, plus the ones that carry
# credibility. `section_of()` in sections.py maps a heading onto one of these.
class Section(str, Enum):
    FRONT = "front"
    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    BACKGROUND = "background"
    RELATED_WORK = "related_work"
    METHODS = "methods"
    DATA = "data"
    EXPERIMENTS = "experiments"
    RESULTS = "results"
    DISCUSSION = "discussion"
    LIMITATIONS = "limitations"
    CONCLUSION = "conclusion"
    ETHICS = "ethics"
    ACKNOWLEDGEMENTS = "acknowledgements"
    REFERENCES = "references"
    APPENDIX = "appendix"
    UNKNOWN = "unknown"


@dataclass
class BBox:
    """A region on a page, in PDF points with the origin at the top left.

    PDF's native origin is bottom left, so every parser converts on the way in.
    Doing it once here means the reader overlay does not need to know which
    parser produced a box, and a box can be handed to a browser unchanged.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def as_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_any(cls, value: Any) -> "BBox | None":
        if value is None:
            return None
        if isinstance(value, BBox):
            return value
        if isinstance(value, dict):
            try:
                return cls(
                    float(value["x0"]), float(value["y0"]),
                    float(value["x1"]), float(value["y1"]),
                )
            except (KeyError, TypeError, ValueError):
                return None
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            try:
                x0, y0, x1, y1 = (float(v) for v in value[:4])
            except (TypeError, ValueError):
                return None
            return cls(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        return None


@dataclass
class Table:
    """A table in every form something downstream might need.

    `grid` is the one that matters: a list of rows of cell strings, which is
    what makes a table searchable cell by cell and renderable without trusting
    parser HTML in a browser. `html` and `markdown` are kept because a model
    reads a markdown table far more reliably than a flattened grid, and because
    losing the parser's own rendering makes a bad parse impossible to diagnose.
    """

    grid: list[list[str]] = field(default_factory=list)
    html: str = ""
    markdown: str = ""
    num_rows: int = 0
    num_cols: int = 0
    # True when the first row is a header. Parsers disagree about this often
    # enough that it is recorded rather than assumed.
    has_header: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "grid": self.grid,
            "html": self.html,
            "markdown": self.markdown,
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "has_header": self.has_header,
        }

    def cell_text(self) -> str:
        """Every cell as one string, for lexical indexing.

        A results table is searched for the value inside it, so the cells have
        to reach the index as text even though the table is stored as a grid.
        """
        return " ".join(cell for row in self.grid for cell in row if cell)


@dataclass
class Image:
    """A figure, with the bytes kept out of the element itself.

    Images are written to the blob store and referenced by digest, because a
    paper with sixty figures otherwise turns every query that touches its rows
    into tens of megabytes of transfer.
    """

    digest: str = ""
    media_type: str = "image/png"
    width: int = 0
    height: int = 0
    # Text read out of the figure by OCR. A chart's axis labels and legend are
    # frequently the only place a number appears, so this is indexed.
    ocr_text: str = ""
    # A vision model's description, written only if the LLM enrichment pass ran.
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "ocr_text": self.ocr_text,
            "description": self.description,
        }


@dataclass
class Element:
    """One extracted thing, and the single unit of storage and citation."""

    kind: Kind
    text: str = ""
    page: int = 0                       # 1 based, 0 when the parser did not say
    bbox: BBox | None = None
    order: int = 0                      # reading order across the document
    # The heading trail this element sits under, outermost first, for example
    # ["3 Method", "3.2 Training"]. Prepended to the chunk at index time, which
    # is the cheap half of contextual retrieval.
    section_path: list[str] = field(default_factory=list)
    section: Section = Section.UNKNOWN
    level: int = 0                      # heading depth, 0 for non headings
    caption: str = ""                   # for a figure or table
    label: str = ""                     # "Figure 3", "Table 2", as printed
    table: Table | None = None
    image: Image | None = None
    # Set on a caption that was matched to its figure, and on a figure that was
    # matched to its caption, so the two can always be shown together.
    linked_id: str = ""
    # Whatever the parser knew that this model has no field for. Kept so a
    # parser upgrade does not silently discard information.
    extra: dict[str, Any] = field(default_factory=dict)

    def search_text(self) -> str:
        """Everything about this element that a lexical index should see."""
        parts = [self.label, self.caption, self.text]
        if self.table:
            parts.append(self.table.cell_text())
        if self.image:
            parts.append(self.image.ocr_text)
            parts.append(self.image.description)
        return "\n".join(p for p in parts if p).strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "page": self.page,
            "bbox": self.bbox.as_dict() if self.bbox else None,
            "order": self.order,
            "section_path": self.section_path,
            "section": self.section.value,
            "level": self.level,
            "caption": self.caption,
            "label": self.label,
            "table": self.table.as_dict() if self.table else None,
            "image": self.image.as_dict() if self.image else None,
            "linked_id": self.linked_id,
            "extra": self.extra,
        }


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    # Digest of the rendered page image in the blob store, written for the
    # reader view and for any vision model call. Empty when rendering was
    # skipped, which is the case on the fast path unless asked for.
    render_digest: str = ""
    # True when the page's text layer is empty or near empty, which means the
    # page is a scan and nothing but OCR will read it.
    needs_ocr: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "width": self.width,
            "height": self.height,
            "render_digest": self.render_digest,
            "needs_ocr": self.needs_ocr,
        }


@dataclass
class PaperMeta:
    """What the paper says it is.

    Every field is best effort. A PDF's own metadata is wrong more often than
    it is right, so a title recovered from the first page's largest text beats
    the one in the document properties, and `title_source` records which won.
    """

    title: str = ""
    title_source: str = ""              # "layout", "metadata", "filename"
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    year: int = 0
    venue: str = ""
    keywords: list[str] = field(default_factory=list)
    language: str = ""
    num_pages: int = 0
    toc: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "title_source": self.title_source,
            "authors": self.authors,
            "abstract": self.abstract,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "year": self.year,
            "venue": self.venue,
            "keywords": self.keywords,
            "language": self.language,
            "num_pages": self.num_pages,
            "toc": self.toc,
        }


@dataclass
class ParseResult:
    elements: list[Element] = field(default_factory=list)
    pages: list[PageInfo] = field(default_factory=list)
    meta: PaperMeta = field(default_factory=PaperMeta)
    parser: str = ""
    parser_version: str = ""
    duration_seconds: float = 0.0
    # Things that went wrong but did not stop the parse, shown in the UI so a
    # missing figure is explained rather than simply absent.
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for element in self.elements:
            out[element.kind.value] = out.get(element.kind.value, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "parser": self.parser,
            "parser_version": self.parser_version,
            "duration_seconds": round(self.duration_seconds, 2),
            "warnings": self.warnings,
            "counts": self.counts(),
            "meta": self.meta.as_dict(),
            "pages": [p.as_dict() for p in self.pages],
        }


class ParseError(RuntimeError):
    """A PDF could not be parsed, with a reason worth showing a user."""

    def __init__(self, message: str, *, parser: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.parser = parser
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "parser": self.parser, "hint": self.hint}


class BaseParser:
    """The interface both tiers implement."""

    id: str = ""
    label: str = ""
    capability: str = ""        # the runtime capability this parser needs
    quality: int = 0            # higher wins when the mode is "auto"

    def is_available(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def unavailable_reason(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def parse(self, pdf_path: str, *, log=None) -> ParseResult:  # pragma: no cover
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        available = self.is_available()
        return {
            "id": self.id,
            "label": self.label,
            "available": available,
            "reason": "" if available else self.unavailable_reason(),
            "quality": self.quality,
        }
