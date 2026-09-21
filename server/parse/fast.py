"""The parser that runs everywhere: PyMuPDF for structure, pdfplumber for tables.

This tier has no machine learning in it at all. It reads the PDF's own text
layer, infers headings from font size and weight, recovers figures from the
embedded image objects, and asks pdfplumber for ruled tables. On a typical
two column paper it finishes in a couple of seconds and produces something
genuinely usable, which matters because it is the only tier a serverless
deployment or a 512 MB container can run.

Where it is weaker than the deep tier, and honestly so:

* A table with no ruling lines is found by pdfplumber's text strategy, which
  guesses column boundaries from whitespace and gets multi-line cells wrong.
  TableFormer in the deep tier reads the structure properly.
* Figures are recovered as embedded image objects. A chart drawn in vector
  primitives, which is most charts produced by matplotlib or TikZ, is not an
  image object at all, so it is recovered by rasterising the region a caption
  points at instead. That works, but the crop is a guess.
* Reading order in a two column layout comes from PyMuPDF's own sort, which is
  correct for ordinary papers and wrong for unusual layouts.

Every one of those is a reason to offer the deep tier, not a reason to skip
this one.
"""
from __future__ import annotations

import io
import re
import statistics
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
from .formulas import body_fonts, equation_number, is_display_formula, strip_number
from .citations import link_markers, split_references
from .sections import section_for_path
from .tables import find_tables, table_bands

# A caption starts with one of these and a number. Used both to label a figure
# and to find the figure a caption belongs to.
_CAPTION_RE = re.compile(
    r"^\s*(fig(?:ure)?|table|tab|chart|scheme|algorithm|listing|eq(?:uation)?|"
    r"appendix|supplementary\s+(?:figure|table))\.?\s*"
    r"([0-9]+(?:\.[0-9]+)*|[ivxlcdm]+|[a-z])\b[.:)\s]*",
    re.IGNORECASE,
)

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b", re.IGNORECASE)
_ARXIV_RE = re.compile(r"\barxiv[:\s]*([0-9]{4}\.[0-9]{4,5})(v[0-9]+)?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19[89][0-9]|20[0-9]{2})\b")

# Text that sits on page one looking like a title and is not one. The arXiv
# stamp is set sideways in the left margin in a large font, so it beats the
# real title on every heuristic that looks at size and position, and a paper
# ingested from arXiv ends up named "arXiv:2501.17887v1 [cs.CL] 27 Jan 2025".
_NOT_A_TITLE = re.compile(
    r"^\s*(arxiv[:\s]|preprint\b|under review\b|submitted to\b|"
    r"published as a (conference|workshop) paper\b|to appear in\b|"
    r"proceedings of\b|copyright\b|licen[sc]ed under\b|doi[:\s]|"
    r"\d{4}\s+\d+(st|nd|rd|th)\b|https?://)",
    re.IGNORECASE,
)

# A heading is short. A paragraph that happens to be set in a larger font, a
# pull quote for example, is not a heading, and this is what separates them.
_MAX_HEADING_CHARS = 140
# Text this close to the top or bottom edge, as a fraction of page height, is a
# running head or a footer rather than content.
_MARGIN_FRACTION = 0.055


def _fitz():
    """PyMuPDF, imported lazily so this module loads without it installed.

    The package is `pymupdf`; `fitz` is the old name and still works but emits
    a deprecation warning on every import, so the new name is tried first.
    """
    try:
        import pymupdf  # noqa: PLC0415

        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # noqa: PLC0415

        return fitz
    except ImportError as exc:
        raise ParseError(
            "PyMuPDF is not installed.",
            parser="fast",
            hint="Run: pip install -r requirements.txt",
        ) from exc


class FastParser(BaseParser):
    id = "fast"
    label = "Fast (PyMuPDF and pdfplumber)"
    capability = "fast_parse"
    quality = 4

    def is_available(self) -> bool:
        return runtime().can("fast_parse")

    def unavailable_reason(self) -> str:
        return runtime().reason("fast_parse")

    def parse(self, pdf_path: str, *, log=None) -> ParseResult:
        started = time.monotonic()
        fitz = _fitz()
        result = ParseResult(parser=self.id, parser_version=getattr(fitz, "__doc__", "") or "")

        def say(message: str) -> None:
            if log:
                log(message)

        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            raise ParseError(
                f"This file could not be opened as a PDF: {exc}",
                parser=self.id,
                hint="Check that the upload is a PDF and is not password protected.",
            ) from exc

        if doc.needs_pass:
            doc.close()
            raise ParseError(
                "This PDF is password protected.",
                parser=self.id,
                hint="Remove the password and upload it again.",
            )

        try:
            say(f"Reading {doc.page_count} pages")
            # Table geometry is found before the text is grouped, because a
            # ruling line is the only reliable signal that a caption has ended
            # and a table has begun. Without it a caption set in body font
            # keeps absorbing the table's own rows and ends up with a bounding
            # box that overlaps the table it is meant to label.
            bands = {
                number: table_bands(doc[number - 1])
                for number in range(1, doc.page_count + 1)
            }
            spans = self._collect_spans(doc, result)
            body_size = self._body_font_size(spans)
            body_font_set = body_fonts(spans)
            body_width = self._body_width(spans)
            elements = self._elements_from_spans(
                spans, body_size, bands, body_font_set, body_width
            )
            self._attach_sections(elements)
            say(f"Recovered {len(elements)} text elements")

            figures = self._extract_figures(doc, elements, result)
            say(f"Recovered {len(figures)} figures")

            tables = self._extract_tables(doc, result)
            say(f"Recovered {len(tables)} tables")

            elements.extend(figures)
            elements.extend(tables)
            elements.sort(key=lambda e: (e.page, e.bbox.y0 if e.bbox else 0.0))
            for position, element in enumerate(elements):
                element.order = position

            self._link_captions(elements)
            self._attach_sections(elements)

            # Sections have to be settled first: splitting only applies to the
            # references section, and knowing which elements are in it is what
            # `_attach_sections` just worked out.
            elements = split_references(elements)
            for position, element in enumerate(elements):
                element.order = position
            link_markers(elements)

            result.elements = elements
            result.meta = self._metadata(doc, elements, pdf_path)
            result.pages = self._pages(doc)
        finally:
            doc.close()

        result.duration_seconds = time.monotonic() - started
        return result

    # ------------------------------------------------------------- text layer

    def _collect_spans(self, doc, result: ParseResult) -> list[dict[str, Any]]:
        """Every text span with its font, size, weight and position.

        Spans rather than blocks, because heading detection needs the font of
        the first span and PyMuPDF's block text has already thrown that away.
        """
        spans: list[dict[str, Any]] = []
        for index in range(doc.page_count):
            try:
                page = doc[index]
                page_height = float(page.rect.height)
                # sort=True gives reading order rather than the order the
                # objects happen to appear in the content stream, which in a
                # two column layout is usually interleaved nonsense.
                data = page.get_text("dict", sort=True)
            except Exception as exc:  # noqa: BLE001 - one bad page is survivable
                result.warnings.append(f"Page {index + 1} text could not be read: {exc}")
                continue

            for block in data.get("blocks", []):
                if block.get("type") != 0:  # 0 is text, 1 is an image
                    continue
                for line in block.get("lines", []):
                    line_spans = line.get("spans", [])
                    if not line_spans:
                        continue
                    text = "".join(s.get("text", "") for s in line_spans)
                    if not text.strip():
                        continue
                    first = line_spans[0]
                    x0, y0, x1, y1 = (float(v) for v in line.get("bbox", (0, 0, 0, 0)))
                    top_margin = page_height * _MARGIN_FRACTION
                    # Every font in the line, weighted by characters. Formula
                    # detection needs the whole set, because what identifies a
                    # display equation is the absence of the body font, and the
                    # first span of a prose line is a body span either way.
                    font_chars: dict[str, int] = {}
                    for piece in line_spans:
                        name = str(piece.get("font", ""))
                        font_chars[name] = font_chars.get(name, 0) + len(
                            piece.get("text", "")
                        )
                    spans.append({
                        "font_chars": font_chars,
                        "text": text,
                        "page": index + 1,
                        "bbox": BBox(x0, y0, x1, y1),
                        "size": round(float(first.get("size", 0.0)), 1),
                        "font": first.get("font", ""),
                        # PyMuPDF packs style into a bit field; bit 4 is bold.
                        "bold": bool(int(first.get("flags", 0)) & 2 ** 4)
                        or "bold" in str(first.get("font", "")).lower(),
                        "italic": bool(int(first.get("flags", 0)) & 2 ** 1),
                        "in_margin": y0 < top_margin or y1 > page_height - top_margin,
                        "block": block.get("number", 0),
                    })
        return spans

    @staticmethod
    def _body_font_size(spans: list[dict[str, Any]]) -> float:
        """The size most of the text is set in.

        The mode, not the mean: a paper with a large title and a page of tiny
        footnotes has a mean that matches no actual text on any page. Weighted
        by characters so that one long paragraph outvotes twenty headings.
        """
        weights: dict[float, int] = {}
        for span in spans:
            if span["in_margin"]:
                continue
            weights[span["size"]] = weights.get(span["size"], 0) + len(span["text"])
        if not weights:
            return 10.0
        return max(weights.items(), key=lambda kv: kv[1])[0]

    @staticmethod
    def _body_width(spans: list[dict[str, Any]]) -> float:
        """How wide a full line of prose is, in points.

        The widest common line width rather than the page width, because the
        test that matters is "does this reach the right margin", and the margin
        is set by the text block, not the paper size.
        """
        widths = sorted(
            (s["bbox"].width for s in spans if not s["in_margin"] and len(s["text"]) > 40),
            reverse=True,
        )
        if not widths:
            return 0.0
        # The 90th percentile, so one over-long line does not set the bar.
        return widths[len(widths) // 10]

    def _elements_from_spans(
        self,
        spans: list[dict[str, Any]],
        body_size: float,
        bands: dict[int, list[tuple[float, float, float, float]]] | None = None,
        body_font_set: frozenset[str] = frozenset(),
        body_width: float = 0.0,
    ) -> list[Element]:
        """Group lines into headings, paragraphs, captions and running heads.

        A line joins the paragraph above it when it shares the font and sits on
        the same page in the same block. Ending a paragraph on a font change is
        what keeps an inline heading from being swallowed by the text before it.
        """
        elements: list[Element] = []
        buffer: list[dict[str, Any]] = []

        def flush() -> None:
            if not buffer:
                return
            text = clean_pdf_text(" ".join(s["text"] for s in buffer))
            if not text:
                buffer.clear()
                return
            first = buffer[0]
            bbox = BBox(
                min(s["bbox"].x0 for s in buffer),
                min(s["bbox"].y0 for s in buffer),
                max(s["bbox"].x1 for s in buffer),
                max(s["bbox"].y1 for s in buffer),
            )
            if first.get("is_formula") and not _inside_band(first["page"], bbox, bands):
                number = equation_number(text)
                elements.append(Element(
                    kind=Kind.FORMULA,
                    text=strip_number(text) or text,
                    page=first["page"],
                    bbox=bbox,
                    label=f"Equation {number}" if number else "",
                    extra={"source": "font-isolation", "number": number},
                ))
                buffer.clear()
                return
            kind, level, label = self._classify(text, first, body_size)
            if _inside_band(first["page"], bbox, bands):
                # The table element already carries this text as cells. Keeping
                # it as a separate searchable element rather than dropping it
                # means a query still matches a row that the grid got wrong,
                # but calling it prose would splice table rows into a sentence.
                kind, level = Kind.OTHER, 0
            element = Element(
                kind=kind,
                text=text,
                page=first["page"],
                bbox=bbox,
                level=level,
                label=label,
            )
            if kind is Kind.CAPTION:
                element.caption = text
            elements.append(element)
            buffer.clear()

        for span in spans:
            span["is_formula"] = is_display_formula(span, body_font_set, body_width)
            if buffer:
                previous = buffer[-1]
                # A gap of more than about one blank line ends the run. Without
                # this a caption keeps absorbing whatever is set in the same
                # font below it, which on a results page means the caption
                # swallows the table and its bounding box then overlaps the
                # table it is supposed to be labelling.
                line_height = max(6.0, previous["bbox"].height)
                vertical_gap = span["bbox"].y0 - previous["bbox"].y1
                same_run = (
                    span["page"] == previous["page"]
                    and abs(span["size"] - previous["size"]) < 0.6
                    and span["bold"] == previous["bold"]
                    and span["in_margin"] == previous["in_margin"]
                    and vertical_gap < line_height * 0.9
                    and not _crosses_band(
                        span["page"], previous["bbox"].y1, span["bbox"].y0, bands
                    )
                    # A display equation never joins the prose around it, and
                    # the lines of a multi line equation always join each other:
                    # a fraction arrives as three separate lines that are one
                    # formula.
                    and span["is_formula"] == previous["is_formula"]
                )
                if span["is_formula"] and previous["is_formula"]:
                    # A fraction arrives as numerator, rule and denominator on
                    # three lines whose boxes overlap vertically, so a positive
                    # gap test alone splits one equation into three.
                    same_run = span["page"] == previous["page"] and (
                        vertical_gap < line_height * 2.5
                    )
                # A caption is its own element even when it is set in body font,
                # so a line that starts one always breaks the run.
                if not same_run or _CAPTION_RE.match(span["text"]):
                    flush()
            buffer.append(span)
        flush()
        return elements

    @staticmethod
    def _classify(
        text: str, span: dict[str, Any], body_size: float
    ) -> tuple[Kind, int, str]:
        """Decide what one run of text is from its font and its shape."""
        stripped = text.strip()
        caption = _CAPTION_RE.match(stripped)
        if caption:
            word = caption.group(1).lower()
            number = caption.group(2)
            prefix = "Table" if word.startswith("tab") else (
                "Equation" if word.startswith("eq") else caption.group(1).title()
            )
            return Kind.CAPTION, 0, f"{prefix} {number}"

        if span["in_margin"]:
            # A margin line that is only a number is a page number. One with
            # words is a running head, which is worth keeping for the journal
            # name it usually carries.
            page_half = span["bbox"].y0 < 200
            return (Kind.PAGE_HEADER if page_half else Kind.PAGE_FOOTER), 0, ""

        size = span["size"]
        is_short = len(stripped) <= _MAX_HEADING_CHARS
        # A heading either stands out by size, or is bold and short and does
        # not end in a full stop. The last test is what rejects a bold lead-in
        # sentence, which is common in the introduction of a survey.
        by_size = size >= body_size + 0.8
        by_weight = span["bold"] and size >= body_size - 0.2
        ends_like_prose = stripped.endswith((".", ",", ";", ":")) and len(stripped) > 60

        if is_short and (by_size or by_weight) and not ends_like_prose:
            if size >= body_size + 5.0:
                level = 1
            elif size >= body_size + 2.5:
                level = 2
            elif size >= body_size + 0.8:
                level = 3
            else:
                level = 4
            return Kind.HEADING, level, ""

        if stripped.startswith(("- ", "* ", "\u2022")) or re.match(r"^\(?[0-9a-z][.)]\s", stripped):
            return Kind.LIST_ITEM, 0, ""

        return Kind.PARAGRAPH, 0, ""

    @staticmethod
    def _attach_sections(elements: list[Element]) -> None:
        """Walk the elements in order, carrying the current heading trail.

        A heading at level 2 replaces everything from level 2 down, which is
        what makes the trail a real path rather than an ever growing list.
        """
        trail: list[tuple[int, str]] = []
        for element in elements:
            if element.kind is Kind.HEADING:
                level = element.level or 3
                trail = [(lv, text) for lv, text in trail if lv < level]
                trail.append((level, element.text))
                element.section_path = [text for _, text in trail[:-1]]
            else:
                element.section_path = [text for _, text in trail]
            path = element.section_path + (
                [element.text] if element.kind is Kind.HEADING else []
            )
            element.section = section_for_path(path)

    # --------------------------------------------------------------- figures

    def _extract_figures(self, doc, elements: list[Element], result: ParseResult) -> list[Element]:
        """Embedded raster images, plus rasterised crops for vector figures.

        Two passes, because the two kinds of figure need opposite treatment. A
        photograph or a screenshot is an embedded image object and is extracted
        losslessly. A chart drawn with vector primitives has no image object at
        all, so the region a caption points at is rendered instead.
        """
        fitz = _fitz()
        figures: list[Element] = []
        seen_digests: set[str] = set()

        for index in range(doc.page_count):
            page = doc[index]
            page_rect = page.rect
            page_area = float(page_rect.width * page_rect.height) or 1.0

            for info in page.get_images(full=True):
                xref = info[0]
                try:
                    rects = page.get_image_rects(xref)
                except Exception:  # noqa: BLE001 - a broken xref is survivable
                    rects = []
                try:
                    pixmap = fitz.Pixmap(doc, xref)
                    if pixmap.n - pixmap.alpha >= 4:  # CMYK has no PNG encoding
                        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                    data = pixmap.tobytes("png")
                    width, height = pixmap.width, pixmap.height
                    pixmap = None
                except Exception as exc:  # noqa: BLE001
                    result.warnings.append(
                        f"Figure on page {index + 1} could not be decoded: {exc}"
                    )
                    continue

                # Background textures, rules and logos. A page sized image is
                # almost always a scan or a watermark, not a figure.
                if width < 48 or height < 48:
                    continue
                digest = blobs.put(data, "image/png")
                if digest in seen_digests:
                    continue
                seen_digests.add(digest)

                bbox = None
                if rects:
                    r = rects[0]
                    bbox = BBox(float(r.x0), float(r.y0), float(r.x1), float(r.y1))
                    if bbox.width * bbox.height > page_area * 0.92:
                        continue

                figures.append(Element(
                    kind=Kind.FIGURE,
                    page=index + 1,
                    bbox=bbox,
                    image=Image(digest=digest, media_type="image/png",
                                width=width, height=height),
                    extra={"source": "embedded"},
                ))

            figures.extend(self._render_vector_figures(page, index + 1, elements, figures))

        return figures

    def _render_vector_figures(
        self, page, page_number: int, elements: list[Element], found: list[Element]
    ) -> list[Element]:
        """Rasterise the region above a caption that no image object covers.

        This is the only way to recover a matplotlib or TikZ chart, which is
        drawn as thousands of vector primitives and has no image object at all.
        The region is a guess: everything between the bottom of whatever is
        above and the top of the caption, clipped to the caption's column. It
        is wrong sometimes, and a figure that is slightly over-cropped is still
        far better than a figure that is missing.
        """
        fitz = _fitz()
        page_captions = [
            element for element in elements
            if element.page == page_number
            and element.kind is Kind.CAPTION
            and element.label.lower().startswith(("figure", "fig", "chart", "scheme"))
        ]
        if not page_captions:
            return []

        covered = [f.bbox for f in found if f.page == page_number and f.bbox]
        out: list[Element] = []

        for caption in page_captions:
            if not caption.bbox:
                continue
            # The figure sits above its caption in every convention this will
            # meet. Stop at the nearest thing above, so a second figure's
            # caption does not become part of this figure's crop.
            ceiling = float(page.rect.y0)
            for other in list(elements) + list(found):
                if other.page != page_number or other is caption or not other.bbox:
                    continue
                if other.bbox.y1 <= caption.bbox.y0 and other.bbox.y1 > ceiling:
                    horizontal_overlap = min(other.bbox.x1, caption.bbox.x1) - max(
                        other.bbox.x0, caption.bbox.x0
                    )
                    if horizontal_overlap > 0.3 * caption.bbox.width:
                        ceiling = other.bbox.y1
            region = BBox(
                max(float(page.rect.x0), caption.bbox.x0 - 12.0),
                ceiling + 2.0,
                min(float(page.rect.x1), caption.bbox.x1 + 12.0),
                caption.bbox.y0 - 2.0,
            )
            if region.height < 40 or region.width < 40:
                continue
            # Already recovered as an embedded image object.
            if any(
                _overlap_ratio(region, box) > 0.5 or _overlap_ratio(box, region) > 0.5
                for box in covered
            ):
                continue

            try:
                matrix = fitz.Matrix(settings.FIGURE_SCALE, settings.FIGURE_SCALE)
                clip = fitz.Rect(region.x0, region.y0, region.x1, region.y1)
                pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
                data = pixmap.tobytes("png")
                width, height = pixmap.width, pixmap.height
            except Exception:  # noqa: BLE001 - a failed render is not fatal
                continue

            digest = blobs.put(data, "image/png")
            out.append(Element(
                kind=Kind.FIGURE,
                page=page_number,
                bbox=region,
                label=caption.label,
                caption=caption.text,
                image=Image(digest=digest, media_type="image/png",
                            width=width, height=height),
                extra={"source": "rendered"},
            ))
            covered.append(region)
        return out

    # ---------------------------------------------------------------- tables

    def _extract_tables(self, doc, result: ParseResult) -> list[Element]:
        """Tables from ruling geometry, with pdfplumber as a second opinion.

        The ruling based finder in tables.py is primary because it is the only
        one of the three that does not hallucinate tables out of body text. A
        page where it finds nothing is retried with pdfplumber's strict lines
        strategy, which catches fully ruled grids drawn in a way the drawings
        list does not expose, and nothing is retried with a text strategy at
        all: that was measured turning page 3 of the Transformer paper into a
        22 by 8 table of word fragments.

        Every accepted region is also rendered to an image. A grid this tier
        gets wrong is still a table the reader can see and a vision model can
        read, and the confidence score tells the UI which it is looking at.
        """
        fitz = _fitz()
        out: list[Element] = []

        for index in range(doc.page_count):
            page = doc[index]
            try:
                found = find_tables(page)
            except Exception as exc:  # noqa: BLE001 - one bad page is survivable
                result.warnings.append(f"Tables on page {index + 1} failed: {exc}")
                continue

            for entry in found:
                x0, y0, x1, y1 = entry["bbox"]
                grid = entry["grid"]
                confidence = entry["confidence"]
                digest, width, height = self._render_region(page, fitz, x0, y0, x1, y1)
                out.append(Element(
                    kind=Kind.TABLE,
                    page=index + 1,
                    bbox=BBox(x0, y0, x1, y1),
                    table=Table(
                        grid=grid,
                        markdown=_grid_to_markdown(grid),
                        html=_grid_to_html(grid),
                        num_rows=len(grid),
                        num_cols=max(len(row) for row in grid),
                    ),
                    image=Image(digest=digest, media_type="image/png",
                                width=width, height=height) if digest else None,
                    extra={
                        "source": "rules",
                        "structure_confidence": confidence,
                        # Below this the grid is shown as a warning in the UI and
                        # the rendered image is what goes to a vision model.
                        "structure_confident": confidence >= 0.55,
                    },
                ))

        pages_with_tables = {e.page for e in out}
        missing = [i + 1 for i in range(doc.page_count) if i + 1 not in pages_with_tables]
        if missing:
            out.extend(self._pdfplumber_tables(doc, missing, result))
        return out

    def _render_region(self, page, fitz, x0, y0, x1, y1):
        """Rasterise a region, so a table or figure is always viewable."""
        try:
            matrix = fitz.Matrix(settings.FIGURE_SCALE, settings.FIGURE_SCALE)
            clip = fitz.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2)
            pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            return blobs.put(pixmap.tobytes("png"), "image/png"), pixmap.width, pixmap.height
        except Exception:  # noqa: BLE001 - a failed render is not fatal
            return "", 0, 0

    def _pdfplumber_tables(self, doc, pages: list[int], result: ParseResult) -> list[Element]:
        """Fully ruled grids on pages where the geometry finder saw nothing."""
        try:
            import pdfplumber  # noqa: PLC0415 - optional, lazily imported
        except ImportError:
            return []

        fitz = _fitz()
        out: list[Element] = []
        wanted = set(pages)
        try:
            with pdfplumber.open(doc.name) as book:
                for index, page in enumerate(book.pages):
                    if index + 1 not in wanted:
                        continue
                    try:
                        found = page.find_tables(table_settings={
                            "vertical_strategy": "lines_strict",
                            "horizontal_strategy": "lines_strict",
                        })
                    except Exception:  # noqa: BLE001
                        continue
                    for table in found:
                        try:
                            grid = [
                                [(cell or "").replace(chr(10), " ").strip() for cell in row]
                                for row in table.extract()
                            ]
                        except Exception:  # noqa: BLE001
                            continue
                        grid = [row for row in grid if any(row)]
                        if len(grid) < 2 or len(grid[0]) < 2:
                            continue
                        x0, y0, x1, y1 = (float(v) for v in table.bbox)
                        digest, width, height = self._render_region(
                            doc[index], fitz, x0, y0, x1, y1
                        )
                        out.append(Element(
                            kind=Kind.TABLE,
                            page=index + 1,
                            bbox=BBox(x0, y0, x1, y1),
                            table=Table(
                                grid=grid,
                                markdown=_grid_to_markdown(grid),
                                html=_grid_to_html(grid),
                                num_rows=len(grid),
                                num_cols=max(len(row) for row in grid),
                            ),
                            image=Image(digest=digest, media_type="image/png",
                                        width=width, height=height) if digest else None,
                            extra={"source": "pdfplumber-lines",
                                   "structure_confidence": 0.8,
                                   "structure_confident": True},
                        ))
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            result.warnings.append(f"Ruled table extraction failed: {exc}")
        return out

    # -------------------------------------------------------------- metadata

    def _metadata(self, doc, elements: list[Element], pdf_path: str) -> PaperMeta:
        raw = doc.metadata or {}
        meta = PaperMeta(num_pages=doc.page_count)

        # The largest text on the first page beats the document properties.
        # A PDF's Title field is very often the LaTeX job name, the template's
        # name, or empty, and is wrong more often than it is right.
        first_page = [
            e for e in elements
            if e.page == 1
            and e.kind in (Kind.HEADING, Kind.PARAGRAPH)
            and not _NOT_A_TITLE.match(e.text.strip())
        ]
        if first_page:
            top = max(first_page[:12], key=lambda e: (e.level == 1, -(e.bbox.y0 if e.bbox else 0)))
            candidate = top.text.strip()
            if 10 <= len(candidate) <= 300:
                meta.title, meta.title_source = candidate, "layout"
        if not meta.title:
            candidate = (raw.get("title") or "").strip()
            if len(candidate) >= 10:
                meta.title, meta.title_source = candidate, "metadata"
        if not meta.title:
            from pathlib import Path

            meta.title, meta.title_source = Path(pdf_path).stem, "filename"

        head = "\n".join(e.text for e in elements[:80])
        if doi := _DOI_RE.search(head):
            meta.doi = doi.group(0).rstrip(".")
        if arxiv := _ARXIV_RE.search(head):
            meta.arxiv_id = arxiv.group(1)
        if year := _YEAR_RE.search(head):
            meta.year = int(year.group(1))

        abstract = self._abstract(elements)
        if abstract:
            meta.abstract = abstract

        authors = (raw.get("author") or "").strip()
        if authors:
            meta.authors = [a.strip() for a in re.split(r"[;,]| and ", authors) if a.strip()]

        try:
            meta.toc = [
                {"level": level, "title": title, "page": page}
                for level, title, page in (doc.get_toc() or [])
            ]
        except Exception:  # noqa: BLE001 - an absent outline is normal
            meta.toc = []
        return meta

    @staticmethod
    def _abstract(elements: list[Element]) -> str:
        """Everything between the Abstract heading and the next heading."""
        collecting = False
        parts: list[str] = []
        for element in elements:
            if element.kind is Kind.HEADING:
                if collecting:
                    break
                if re.match(r"^\s*abstract\b", element.text, re.IGNORECASE):
                    collecting = True
                continue
            if collecting and element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM):
                parts.append(element.text)
        if parts:
            return " ".join(parts)[:4000]

        # Some templates set "Abstract" as a bold lead-in on the paragraph
        # itself rather than as its own line, so it never became a heading.
        for element in elements[:40]:
            match = re.match(r"^\s*abstract[\s.:\u2014-]+(.+)", element.text, re.IGNORECASE)
            if match and len(match.group(1)) > 120:
                return match.group(1)[:4000]
        return ""

    @staticmethod
    def _pages(doc) -> list[PageInfo]:
        pages: list[PageInfo] = []
        for index in range(doc.page_count):
            page = doc[index]
            text = page.get_text("text") or ""
            pages.append(PageInfo(
                number=index + 1,
                width=float(page.rect.width),
                height=float(page.rect.height),
                # A page whose text layer is this sparse is an image of a page.
                needs_ocr=len(text.strip()) < 80,
            ))
        return pages

    # -------------------------------------------------------------- captions

    @staticmethod
    def _link_captions(elements: list[Element]) -> None:
        """Pair each caption with the figure or table it describes.

        Convention says a figure caption sits below its figure and a table
        caption above its table, and most papers follow it. Enough do not,
        including several venue templates that put table captions underneath,
        that hardcoding the direction loses real pairings. So the conventional
        side is searched first and the other side only if that finds nothing,
        which keeps the common case exact without failing the uncommon one.
        """
        by_page: dict[int, list[Element]] = {}
        for element in elements:
            by_page.setdefault(element.page, []).append(element)

        for page_elements in by_page.values():
            captions = [e for e in page_elements if e.kind is Kind.CAPTION and e.bbox]
            # Nearest pairing first, so a page with two figures does not give
            # both captions to whichever figure happens to be checked first.
            for reverse in (False, True):
                for caption in captions:
                    if caption.linked_id:
                        continue
                    is_table = caption.label.lower().startswith("table")
                    target = Kind.TABLE if is_table else Kind.FIGURE
                    best, best_distance = None, 1e9
                    for candidate in page_elements:
                        if candidate.kind is not target or not candidate.bbox:
                            continue
                        if candidate.caption:
                            continue
                        above = caption.bbox.y0 - candidate.bbox.y1
                        below = candidate.bbox.y0 - caption.bbox.y1
                        conventional = below if is_table else above
                        other = above if is_table else below
                        distance = other if reverse else conventional
                        # A caption's glyph box often overhangs the rule that
                        # starts a table by a point or two, so an exact test
                        # rejects the correct pairing.
                        if distance >= -6.0 and distance < best_distance:
                            best, best_distance = candidate, distance
                    limit = 220.0 if not reverse else 90.0
                    if best is not None and best_distance < limit:
                        best.caption = caption.text
                        best.label = best.label or caption.label
                        caption.linked_id = f"{best.page}:{best.order}"


def _inside_band(
    page: int, bbox: BBox, bands: dict[int, list[tuple[float, float, float, float]]] | None
) -> bool:
    for x0, y0, x1, y1 in (bands or {}).get(page, ()):
        if bbox.y0 >= y0 - 2 and bbox.y1 <= y1 + 2 and bbox.x1 > x0 and bbox.x0 < x1:
            return True
    return False


def _crosses_band(
    page: int,
    top: float,
    bottom: float,
    bands: dict[int, list[tuple[float, float, float, float]]] | None,
) -> bool:
    """Whether a table's top or bottom edge falls between two lines of text."""
    for _, y0, _, y1 in (bands or {}).get(page, ()):
        if top <= y0 <= bottom or top <= y1 <= bottom:
            return True
    return False


def _overlap_ratio(a: BBox, b: BBox) -> float:
    """Fraction of `a` that `b` covers."""
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    if width <= 0 or height <= 0:
        return 0.0
    area = a.width * a.height
    return (width * height) / area if area else 0.0


def _grid_to_markdown(grid: list[list[str]]) -> str:
    if not grid:
        return ""
    width = max(len(row) for row in grid)
    padded = [list(row) + [""] * (width - len(row)) for row in grid]
    # Pipes inside a cell would end the column early and shift every value in
    # the row, which is exactly the kind of silent corruption a results table
    # must not suffer.
    def cell(value: str) -> str:
        return (value or "").replace("|", "\\|").replace("\n", " ").strip()

    lines = ["| " + " | ".join(cell(c) for c in padded[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in padded[1:]:
        lines.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(lines)


def _grid_to_html(grid: list[list[str]]) -> str:
    if not grid:
        return ""

    def escape(value: str) -> str:
        return (
            (value or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    rows = ["<tr>" + "".join(f"<th>{escape(c)}</th>" for c in grid[0]) + "</tr>"]
    for row in grid[1:]:
        rows.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"
