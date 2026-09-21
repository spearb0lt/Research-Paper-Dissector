"""The deep tier: Docling's layout model, TableFormer and figure extraction.

Docling was chosen over Marker and MinerU deliberately, and not because it wins
on raw accuracy. On olmOCR-bench as of 2026 Marker 2 leads at 76.0, MinerU is
at 72.7 and Docling's non-VLM pipeline is at 50.3. What Docling has instead:

* MIT licence and Linux Foundation governance. Marker's weights carry a
  commercial restriction and MinerU is AGPL, which decides the question for
  anything the user may want to deploy.
* A structured `DoclingDocument` rather than a stream of markdown. This whole
  application is built on elements with pages, bounding boxes and reading
  order, and reconstructing those from markdown is a downgrade.
* A real CPU path. The other two assume a GPU to be worth running.
* An optional VLM backend (granite-docling, 258M) that closes most of the
  accuracy gap and fits in 4 GB of VRAM, where a ColPali class model does not.

The honest summary is that Docling is the best fit for this shape of
application, not that it is the most accurate parser in existence. If that
changes, `registry.py` is where a fourth parser gets added, and nothing
downstream of `ParseResult` has to know.
"""
from __future__ import annotations

import io
import time
from typing import Any

from .. import blobs, settings
from ..runtime import current as runtime
from ..util import clean_pdf_text
from .base import (
    BaseParser,
    BBox,
    Element,
    Image,
    Kind,
    PageInfo,
    PaperMeta,
    ParseError,
    ParseResult,
    Table,
)
from .citations import link_markers, split_references
from .sections import section_for_path

# Docling's own labels, mapped onto this application's element kinds. A label
# missing from here becomes OTHER, which still indexes and still displays, so a
# Docling upgrade that adds a label degrades rather than breaks.
_LABEL_MAP: dict[str, Kind] = {
    "title": Kind.TITLE,
    "section_header": Kind.HEADING,
    "text": Kind.PARAGRAPH,
    "paragraph": Kind.PARAGRAPH,
    "list_item": Kind.LIST_ITEM,
    "caption": Kind.CAPTION,
    "footnote": Kind.FOOTNOTE,
    "reference": Kind.REFERENCE,
    "formula": Kind.FORMULA,
    "code": Kind.CODE,
    "page_header": Kind.PAGE_HEADER,
    "page_footer": Kind.PAGE_FOOTER,
    "form": Kind.FORM_FIELD,
    "key_value_region": Kind.FORM_FIELD,
    "checkbox_selected": Kind.FORM_FIELD,
    "checkbox_unselected": Kind.FORM_FIELD,
    "document_index": Kind.OTHER,
    "picture": Kind.FIGURE,
    "table": Kind.TABLE,
}


class DeepParser(BaseParser):
    id = "deep"
    label = "Deep (Docling layout model and TableFormer)"
    capability = "deep_parse"
    quality = 9

    def is_available(self) -> bool:
        return runtime().can("deep_parse")

    def unavailable_reason(self) -> str:
        return runtime().reason("deep_parse")

    def parse(self, pdf_path: str, *, log=None) -> ParseResult:
        started = time.monotonic()

        def say(message: str) -> None:
            if log:
                log(message)

        if not self.is_available():
            raise ParseError(
                "Deep parsing is not available here.",
                parser=self.id,
                hint=self.unavailable_reason(),
            )

        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling_core.types.doc import DocItemLabel, PictureItem, TableItem
        except ImportError as exc:
            raise ParseError(
                "Docling is not installed.",
                parser=self.id,
                hint="Run: pip install -r requirements-server.txt",
            ) from exc

        options = PdfPipelineOptions()
        options.do_table_structure = True
        options.generate_picture_images = True
        options.generate_table_images = True
        options.images_scale = settings.FIGURE_SCALE
        # Docling's own OCR is left off and handled in ocr.py instead, so that
        # one OCR engine serves both tiers and a scanned page reads the same
        # whichever parser produced it.
        options.do_ocr = False
        options.do_formula_enrichment = settings.FORMULA_ENRICHMENT
        options.do_code_enrichment = settings.FORMULA_ENRICHMENT
        try:
            # TableFormer's accurate mode is several times slower than fast and
            # is what the deep tier is for, so it is the default here.
            options.table_structure_options.do_cell_matching = True
            options.table_structure_options.mode = _accurate_mode()
        except Exception:  # noqa: BLE001 - option names drift between releases
            pass

        say("Loading the layout and table structure models")
        try:
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            converted = converter.convert(pdf_path)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            raise ParseError(
                f"Docling could not convert this PDF: {exc}",
                parser=self.id,
                hint=(
                    "The first run downloads roughly 500 MB of model weights. "
                    "Check the machine has network access and disk space."
                ),
            ) from exc

        document = converted.document
        result = ParseResult(parser=self.id, parser_version=_docling_version())
        elements: list[Element] = []

        say("Reading the document model")
        for item, _level in document.iterate_items():
            page = item.prov[0].page_no if getattr(item, "prov", None) else 0
            bbox = _bbox_of(item, document)

            if isinstance(item, TableItem):
                element = self._table(item, document, page, bbox, result)
            elif isinstance(item, PictureItem):
                element = self._picture(item, document, page, bbox, result)
            else:
                element = self._text(item, page, bbox)
            if element is not None:
                elements.append(element)

        elements.sort(key=lambda e: (e.page, e.bbox.y0 if e.bbox else 0.0))
        for position, element in enumerate(elements):
            element.order = position

        self._attach_sections(elements)
        elements = split_references(elements)
        for position, element in enumerate(elements):
            element.order = position
        link_markers(elements)
        result.elements = elements
        result.meta = self._metadata(document, elements, pdf_path)
        result.pages = self._pages(pdf_path, document, result)
        result.duration_seconds = time.monotonic() - started
        say(f"Recovered {len(elements)} elements")
        return result

    # ----------------------------------------------------------------- items

    def _text(self, item, page: int, bbox: BBox | None) -> Element | None:
        label = str(getattr(getattr(item, "label", None), "value", "") or "")
        text = clean_pdf_text(getattr(item, "text", "") or "")
        if not text:
            return None
        kind = _LABEL_MAP.get(label, Kind.OTHER)
        # A running head or a page number is kept, because it carries the venue
        # and the page, but it must never join the narrative reading order.
        if kind is Kind.PARAGRAPH and len(text) < 3:
            return None
        level = 0
        if kind is Kind.HEADING:
            level = int(getattr(item, "level", 0) or 0) or 2
        return Element(
            kind=kind, text=text, page=page, bbox=bbox, level=level,
            caption=text if kind is Kind.CAPTION else "",
            extra={"docling_label": label},
        )

    def _table(self, item, document, page: int, bbox: BBox | None, result: ParseResult) -> Element:
        grid: list[list[str]] = []
        markdown = html = ""
        try:
            html = item.export_to_html(document)
        except Exception:  # noqa: BLE001 - one export failing is survivable
            pass
        try:
            markdown = item.export_to_markdown(document)
        except Exception:  # noqa: BLE001
            pass
        try:
            frame = item.export_to_dataframe(document)
            grid = [[clean_pdf_text(str(c)) for c in frame.columns]]
            grid.extend([[("" if v is None else clean_pdf_text(str(v))) for v in row]
                         for row in frame.itertuples(index=False)])
        except Exception:  # noqa: BLE001 - pandas is optional
            grid = _grid_from_cells(item)

        caption = ""
        try:
            caption = item.caption_text(document) or ""
        except Exception:  # noqa: BLE001
            pass

        digest = width = height = 0
        digest = ""
        try:
            picture = item.get_image(document)
            if picture is not None:
                digest, width, height = _store(picture)
        except Exception:  # noqa: BLE001 - a table image is a nicety
            pass

        return Element(
            kind=Kind.TABLE, page=page, bbox=bbox, caption=caption,
            label=_label_from_caption(caption),
            table=Table(
                grid=grid, html=html, markdown=markdown,
                num_rows=len(grid), num_cols=max((len(r) for r in grid), default=0),
            ),
            image=Image(digest=digest, media_type="image/png",
                        width=width, height=height) if digest else None,
            # TableFormer read the structure rather than guessing it, so unlike
            # the fast tier this does not need a confidence caveat.
            extra={"source": "tableformer", "structure_confidence": 0.95,
                   "structure_confident": True},
        )

    def _picture(self, item, document, page: int, bbox: BBox | None, result: ParseResult) -> Element | None:
        try:
            picture = item.get_image(document)
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"Figure on page {page} could not be read: {exc}")
            return None
        if picture is None:
            return None
        digest, width, height = _store(picture)
        if not digest or width < 48 or height < 48:
            return None
        caption = ""
        try:
            caption = item.caption_text(document) or ""
        except Exception:  # noqa: BLE001
            pass
        return Element(
            kind=Kind.FIGURE, page=page, bbox=bbox, caption=caption,
            label=_label_from_caption(caption),
            image=Image(digest=digest, media_type="image/png", width=width, height=height),
            extra={"source": "docling",
                   "docling_label": str(getattr(getattr(item, "label", None), "value", ""))},
        )

    # -------------------------------------------------------------- assembly

    @staticmethod
    def _attach_sections(elements: list[Element]) -> None:
        trail: list[tuple[int, str]] = []
        for element in elements:
            if element.kind is Kind.HEADING:
                level = element.level or 2
                trail = [(lv, text) for lv, text in trail if lv < level]
                trail.append((level, element.text))
                element.section_path = [text for _, text in trail[:-1]]
            else:
                element.section_path = [text for _, text in trail]
            path = element.section_path + (
                [element.text] if element.kind is Kind.HEADING else []
            )
            element.section = section_for_path(path)

    def _metadata(self, document, elements: list[Element], pdf_path: str) -> PaperMeta:
        from pathlib import Path

        meta = PaperMeta()
        titles = [e for e in elements if e.kind is Kind.TITLE]
        if titles:
            meta.title, meta.title_source = titles[0].text, "layout"
        else:
            headings = [e for e in elements if e.kind is Kind.HEADING and e.page <= 1]
            if headings:
                meta.title, meta.title_source = headings[0].text, "layout"
        if not meta.title:
            meta.title, meta.title_source = Path(pdf_path).stem, "filename"

        # Reuse the fast tier's front matter mining rather than writing it
        # twice: the regular expressions do not care which parser found the text.
        from .fast import _ARXIV_RE, _DOI_RE, _YEAR_RE

        head = "\n".join(e.text for e in elements[:80])
        if match := _DOI_RE.search(head):
            meta.doi = match.group(0).rstrip(".")
        if match := _ARXIV_RE.search(head):
            meta.arxiv_id = match.group(1)
        if match := _YEAR_RE.search(head):
            meta.year = int(match.group(1))

        collecting = False
        parts: list[str] = []
        for element in elements:
            if element.kind is Kind.HEADING:
                if collecting:
                    break
                if element.text.strip().lower().startswith("abstract"):
                    collecting = True
                continue
            if collecting and element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM):
                parts.append(element.text)
        meta.abstract = " ".join(parts)[:4000]

        meta.num_pages = len(getattr(document, "pages", {}) or {}) or 0
        return meta

    @staticmethod
    def _pages(pdf_path: str, document, result: ParseResult) -> list[PageInfo]:
        """Page geometry, read from the PDF rather than from the document model.

        Docling's page objects carry a size but not consistently across
        versions, and the reader overlay needs exact points, so PyMuPDF is
        asked directly. It is already a dependency and costs milliseconds.
        """
        try:
            from .fast import _fitz

            book = _fitz().open(pdf_path)
        except Exception:  # noqa: BLE001
            return []
        pages: list[PageInfo] = []
        try:
            for index in range(book.page_count):
                page = book[index]
                text = page.get_text("text") or ""
                pages.append(PageInfo(
                    number=index + 1,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    needs_ocr=len(text.strip()) < 80,
                ))
        finally:
            book.close()
        if pages and not result.meta.num_pages:
            result.meta.num_pages = len(pages)
        return pages


# --------------------------------------------------------------- helpers


def _accurate_mode():
    from docling.datamodel.pipeline_options import TableFormerMode

    return TableFormerMode.ACCURATE


def _docling_version() -> str:
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:  # noqa: BLE001
        return ""


def _store(picture) -> tuple[str, int, int]:
    """Write a PIL image to the blob store as PNG."""
    try:
        buffer = io.BytesIO()
        picture.convert("RGB").save(buffer, format="PNG", optimize=True)
        return blobs.put(buffer.getvalue(), "image/png"), picture.width, picture.height
    except Exception:  # noqa: BLE001 - a figure that will not encode is dropped
        return "", 0, 0


def _bbox_of(item, document) -> BBox | None:
    """Docling's bounding box, converted to a top left origin.

    Docling reports in PDF coordinates, whose origin is the bottom left. Every
    consumer here, including the browser overlay, wants top left, so the flip
    happens once at the boundary rather than in each consumer.
    """
    provenance = getattr(item, "prov", None)
    if not provenance:
        return None
    first = provenance[0]
    box = getattr(first, "bbox", None)
    if box is None:
        return None
    page_height = 0.0
    try:
        page = (getattr(document, "pages", {}) or {}).get(first.page_no)
        page_height = float(getattr(getattr(page, "size", None), "height", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        page_height = 0.0

    x0, x1 = float(box.l), float(box.r)
    bottom, top = float(box.b), float(box.t)
    origin = str(getattr(getattr(box, "coord_origin", None), "value", "")).upper()
    if page_height and "BOTTOM" in origin:
        y0, y1 = page_height - top, page_height - bottom
    else:
        y0, y1 = min(top, bottom), max(top, bottom)
    return BBox(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _grid_from_cells(item) -> list[list[str]]:
    """Rebuild a grid from table cells when the dataframe export is unavailable."""
    data = getattr(item, "data", None)
    cells = getattr(data, "table_cells", None) or []
    if not cells:
        return []
    rows = max((int(getattr(c, "end_row_offset_idx", 0)) for c in cells), default=0)
    columns = max((int(getattr(c, "end_col_offset_idx", 0)) for c in cells), default=0)
    if rows < 1 or columns < 1:
        return []
    grid = [["" for _ in range(columns)] for _ in range(rows)]
    for cell in cells:
        row = int(getattr(cell, "start_row_offset_idx", 0))
        column = int(getattr(cell, "start_col_offset_idx", 0))
        if 0 <= row < rows and 0 <= column < columns:
            grid[row][column] = clean_pdf_text(str(getattr(cell, "text", "") or ""))
    return grid


def _label_from_caption(caption: str) -> str:
    from .fast import _CAPTION_RE

    match = _CAPTION_RE.match(caption or "")
    if not match:
        return ""
    word = match.group(1).lower()
    prefix = "Table" if word.startswith("tab") else match.group(1).title()
    return f"{prefix} {match.group(2)}"
