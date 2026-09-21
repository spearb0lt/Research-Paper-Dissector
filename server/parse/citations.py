"""Reference entries, and the in-text markers that point at them.

Two problems, and the second only becomes tractable after the first.

**Splitting the reference list.** A reference list is set as a run of lines in
one font, so the text grouper reads it as a single element:

    '[1] Jimmy Lei Ba, ... Layer normalization. arXiv:1607.06450, 2016. [2] Dzmitry'

That is two references in one row, which makes every one of them unreachable
individually. They are split back apart here on the marker that starts each
entry, which is either a bracketed number, a bare number and a stop, or, in an
author-year list, a new surname at the start of a line.

**Linking markers to entries.** Papers use two citation styles and they need
different matching:

* Numeric: `[13]`, `[12, 13]`, `[4-7]` resolve by the entry's own number.
* Author-year: `(Vaswani et al., 2017)`, `(Cho and Bengio, 2014)` resolve on
  the first author's surname plus the year, because that pair is what the
  style guarantees to be unique within one bibliography.

Both are wanted, because a library will contain papers in both styles and a
reader following a claim to its source does not care which the author used.

Metadata extraction here is deliberately shallow: the number, the year, the
arXiv id, the DOI and any URL, all of which appear verbatim and can be read
without guessing, plus a best effort title. Canonical metadata comes from
resolving against arXiv or Crossref in `server/refs.py`, which is where a
guess can be replaced by an answer.
"""
from __future__ import annotations

import re
from typing import Any

from .base import Element, Kind, Section

# The start of a numbered entry: "[12]" or "12." at a boundary.
_ENTRY_BRACKET = re.compile(r"(?:^|(?<=[\s.]))\[(\d{1,3})\]\s+")
_ENTRY_PLAIN = re.compile(r"(?:^|(?<=[.\s]))(\d{1,3})\.\s+(?=[A-Z])")

# An in-text marker. Ranges and lists are both common: "[4-7]", "[12, 13]".
_MARKER = re.compile(r"\[(\d{1,3}(?:\s*[-,–]\s*\d{1,3})*)\]")
# Author-year, with the variants a style guide allows.
_AUTHOR_YEAR = re.compile(
    r"\(\s*([A-Z][A-Za-z'À-ɏ-]+)"          # first author surname
    r"(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'-]+|&\s*[A-Z][A-Za-z'-]+))?"
    r"[,\s]+(\d{4})[a-z]?\s*\)"
)

_ARXIV = re.compile(r"arxiv[:\s/]*((?:[a-z-]+/)?\d{4}\.\d{4,5}|[a-z-]+/\d{7})", re.IGNORECASE)
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.IGNORECASE)
_URL = re.compile(r"(https?://[^\s,;)\]]+)")
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

# A reference entry is at least this long. Shorter is a stray fragment.
_MIN_ENTRY = 30


def split_references(elements: list[Element]) -> list[Element]:
    """Break merged reference blocks into one element per entry.

    Returns a new list with the reference section rebuilt. Order values are not
    reassigned here: the caller renumbers once, after every pass has run.
    """
    out: list[Element] = []
    for element in elements:
        if element.section is not Section.REFERENCES or not element.text.strip():
            out.append(element)
            continue
        if element.kind in (Kind.HEADING, Kind.TITLE, Kind.PAGE_HEADER, Kind.PAGE_FOOTER):
            out.append(element)
            continue

        pieces = _split_entries(element.text)
        if len(pieces) <= 1:
            element.kind = Kind.REFERENCE
            element.extra = {**element.extra, **parse_reference(element.text)}
            out.append(element)
            continue

        for piece in pieces:
            if len(piece.strip()) < _MIN_ENTRY:
                continue
            child = Element(
                kind=Kind.REFERENCE,
                text=piece.strip(),
                page=element.page,
                # The children share the parent's box: splitting text cannot
                # recover where each entry sat, and a box covering the block is
                # more useful than none for showing where the entry lives.
                bbox=element.bbox,
                section=Section.REFERENCES,
                section_path=list(element.section_path),
                extra={**element.extra, "split_from_block": True},
            )
            child.extra.update(parse_reference(piece))
            if child.extra.get("number"):
                child.label = f"[{child.extra['number']}]"
            out.append(child)
    return out


def _split_entries(text: str) -> list[str]:
    """Split a block of references into entries, on whichever marker it uses."""
    bracket_points = [m.start() for m in _ENTRY_BRACKET.finditer(text)]
    if len(bracket_points) >= 2:
        return _cut(text, bracket_points)

    plain_points = [m.start() for m in _ENTRY_PLAIN.finditer(text)]
    if len(plain_points) >= 2:
        return _cut(text, plain_points)

    return [text]


def _cut(text: str, points: list[int]) -> list[str]:
    bounds = sorted(set(points))
    if bounds and bounds[0] > 0:
        bounds.insert(0, 0)
    pieces: list[str] = []
    for index, start in enumerate(bounds):
        end = bounds[index + 1] if index + 1 < len(bounds) else len(text)
        pieces.append(text[start:end])
    return pieces


def parse_reference(text: str) -> dict[str, Any]:
    """What can be read off a reference entry without asking anything."""
    body = (text or "").strip()
    out: dict[str, Any] = {}

    number = _ENTRY_BRACKET.match(body) or re.match(r"\s*(\d{1,3})[.)]\s+", body)
    if number:
        out["number"] = int(number.group(1))
        body = body[number.end():].strip()

    if match := _ARXIV.search(text):
        out["arxiv_id"] = match.group(1)
    if match := _DOI.search(text):
        out["doi"] = match.group(1).rstrip(".,;")
    if match := _URL.search(text):
        out["url"] = match.group(1).rstrip(".,;")
    years = _YEAR.findall(text)
    if years:
        # The last year in an entry is the publication year; an earlier one is
        # usually part of a volume or a conference name.
        out["year"] = int(years[-1])

    authors, title = _authors_and_title(body)
    if authors:
        out["authors"] = authors
    if title:
        out["title"] = title
    out["raw"] = text.strip()
    return out


def _authors_and_title(body: str) -> tuple[list[str], str]:
    """Best effort split of "Authors. Title. Venue, Year."

    Bibliography formatting is not a solved problem and this does not pretend
    otherwise. It looks for the first sentence boundary that follows a run of
    name-shaped words, which is right for the overwhelming majority of entries
    and wrong for a few. `refs.py` replaces the guess with real metadata where
    the entry can be resolved.
    """
    # An initial like "J." or "Ba," must not end the author run, so the split
    # only fires on a stop followed by a space and a word of 2 or more letters.
    parts = re.split(r"(?<=[a-z\]\)])\.\s+(?=[A-Z0-9])", body, maxsplit=2)
    if not parts:
        return [], ""

    head = parts[0].strip()
    # A head that is very long is probably already the title, because the
    # authors were separated differently.
    if len(head) > 320:
        return [], head[:300]

    authors = [
        name.strip(" .,")
        for name in re.split(r",\s*and\s+|,\s*|\s+and\s+|\s*&\s*", head)
        if name.strip(" .,")
    ]
    # Names are short. Anything long is a title that was mistaken for a name.
    authors = [a for a in authors if 2 <= len(a) <= 60][:24]

    title = _trim_title(parts[1]) if len(parts) > 1 else ""
    return authors, title[:300]


# Where a title stops and the venue starts. The sentence splitter cannot see
# this boundary, because "Layer normalization. arXiv preprint arXiv:1607.06450"
# continues in lower case and so reads as one sentence.
_VENUE_START = re.compile(
    r"\s+(?:arxiv\s+preprint|arxiv:|in\s+proceedings|in\s+advances|in\s+the\s+\d|"
    r"in\s+[A-Z]{2,}|proceedings\s+of|journal\s+of|transactions\s+on|"
    r"url\s+https?://|https?://|doi:|pages?\s+\d|volume\s+\d|vol\.\s*\d)",
    re.IGNORECASE,
)


def _trim_title(text: str) -> str:
    title = (text or "").strip(" .")
    cut = _VENUE_START.search(title)
    if cut and cut.start() > 10:
        title = title[: cut.start()]
    return title.strip(" .,;")


def first_surname(authors: list[str]) -> str:
    """The surname to match an author-year marker against."""
    if not authors:
        return ""
    first = authors[0].replace(".", " ").strip()
    words = [w for w in first.split() if len(w) > 1]
    return words[-1].lower() if words else ""


def link_markers(elements: list[Element]) -> int:
    """Resolve in-text citation markers to reference elements.

    Writes `extra["cites"]` on each citing element as a list of the `order`
    values of the references it points at. Order rather than database id,
    because ids do not exist until the elements are committed and the parser
    must not depend on storage.

    Returns how many markers were resolved.
    """
    references = [e for e in elements if e.kind is Kind.REFERENCE]
    if not references:
        return 0

    by_number: dict[int, Element] = {}
    by_author_year: dict[tuple[str, int], Element] = {}
    for reference in references:
        number = reference.extra.get("number")
        if isinstance(number, int):
            by_number.setdefault(number, reference)
        surname = first_surname(list(reference.extra.get("authors") or []))
        year = reference.extra.get("year")
        if surname and isinstance(year, int):
            by_author_year.setdefault((surname, year), reference)

    resolved = 0
    for element in elements:
        if element.kind is Kind.REFERENCE or not element.text:
            continue
        targets: list[int] = []

        for match in _MARKER.finditer(element.text):
            for number in _expand(match.group(1)):
                target = by_number.get(number)
                if target is not None and target.order not in targets:
                    targets.append(target.order)
                    resolved += 1

        for match in _AUTHOR_YEAR.finditer(element.text):
            key = (match.group(1).lower(), int(match.group(2)))
            target = by_author_year.get(key)
            if target is not None and target.order not in targets:
                targets.append(target.order)
                resolved += 1

        if targets:
            element.extra = {**element.extra, "cites": targets}
    return resolved


def _expand(group: str) -> list[int]:
    """"4-7" becomes 4, 5, 6, 7; "12, 13" becomes 12, 13."""
    numbers: list[int] = []
    for part in re.split(r"\s*,\s*", group):
        span = re.match(r"^(\d{1,3})\s*[-–]\s*(\d{1,3})$", part.strip())
        if span:
            start, end = int(span.group(1)), int(span.group(2))
            # A "range" of hundreds is a misparse, not a citation of 300 works.
            if 0 < end - start <= 30:
                numbers.extend(range(start, end + 1))
                continue
        try:
            numbers.append(int(part.strip()))
        except ValueError:
            continue
    return numbers
