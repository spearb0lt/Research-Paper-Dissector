"""Recovering table structure from a PDF without a layout model.

Scientific papers overwhelmingly use booktabs style: horizontal rules above the
header, below the header and at the foot, and no vertical rules at all. That
breaks every general purpose table finder, because they look for a grid of
intersecting lines and find none. It also breaks the usual fallback of
inferring both axes from whitespace, which on a two column paper happily
"finds" a table in the middle of a paragraph and shreds the prose into cells.

Both failures were measured on real papers before this was written. pdfplumber
and PyMuPDF's own `find_tables` with a text strategy each turned body text on
page 3 of "Attention Is All You Need" into a 22 by 8 table of word fragments.

What works, and what this does:

1. Find horizontal ruling lines, and group ones with matching x-extent into a
   band. Two or more aligned rules within a page is what a table looks like and
   what a paragraph never does, so this alone removes the false positives.
2. Inside the band, cluster words into rows by their vertical centre.
3. Find the column boundaries by projecting every word onto the x-axis and
   taking the runs that no word covers in essentially any row. A results table
   is column aligned by construction, so these gaps are real and sharp.

Measured against five hard tables, including the thirteen column ablation table
in the Transformer paper and the eleven column benchmark table in ColPali, this
recovers the exact column count on four and splits one column too many on the
fifth. The constants below were chosen by sweeping them against those five
rather than by taste.

Where it still loses to TableFormer, which the deep tier uses:

* Cells that span more than one column, and nested headers more than two rows
  deep.
* Tables with no ruling at all, which this does not find.
* A header set *above* the toprule rather than below it. The band runs from the
  first rule to the last, so such a header is outside it and is picked up as
  part of the caption instead. Table 2 of the Transformer paper is laid out
  this way: every data row and the column count come out correct and the
  column names do not. They are not lost, because the caption is stored on the
  table element and travels with it into the index and the prompt, but they are
  not in the grid.

A table whose structure fails validation is still emitted, with its region
rendered as an image and `structure_confident` false, because a table a reader
can see and a vision model can read beats a table that silently went missing.
"""
from __future__ import annotations

import statistics
from typing import Any

from ..util import clean_pdf_text

# Chosen by parameter sweep against five hand-checked tables, not by taste.
# Changing one without re-running that sweep is how this quietly regresses.
_MIN_GAP_CHARS = 0.5      # a column gap is at least half a character wide
_EMPTY_ROW_FRACTION = 0.10  # and is empty in at least 90 percent of rows
_PROJECTION_RESOLUTION = 0.5  # points per bucket in the x-projection

# A rule shorter than this fraction of the page is a leader dot, an underline
# or a fragment of a figure, not a table rule.
_MIN_RULE_WIDTH_FRACTION = 0.10
# Two rules belong to the same table when their x-extents overlap this much.
_RULE_ALIGNMENT = 0.80
# and when they are no further apart than this. A table longer than a page is
# split by the page break anyway, so this does not need to be generous.
_MAX_RULE_GAP = 420.0

_ROW_TOLERANCE = 0.55     # fraction of median glyph height
_WRAP_GAP = 0.72          # a wrapped header line sits this much tighter
_OUTLIER_GAP = 2.2        # a stray heading sits this much further away


def _horizontal_rules(page) -> list[tuple[float, float, float]]:
    """Every horizontal rule on the page as (y, x0, x1).

    Rules reach a PDF as either a stroked line or a filled rectangle a fraction
    of a point high, and different LaTeX backends emit different ones, so both
    are collected. Rules at the same y are merged because a rule drawn in two
    segments, which booktabs does for `cmidrule`, would otherwise read as two.
    """
    width = float(page.rect.width)
    minimum = width * _MIN_RULE_WIDTH_FRACTION
    found: dict[float, tuple[float, float]] = {}

    def add(y: float, x0: float, x1: float) -> None:
        if x1 - x0 < minimum:
            return
        key = round(y, 1)
        if key in found:
            found[key] = (min(found[key][0], x0), max(found[key][1], x1))
        else:
            found[key] = (x0, x1)

    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - a page with no vector content is normal
        return []

    for drawing in drawings:
        for item in drawing.get("items", []):
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.y - end.y) < 1.2:
                    add(min(start.y, end.y), min(start.x, end.x), max(start.x, end.x))
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 2.0:
                    add(rect.y0, rect.x0, rect.x1)
    return sorted((y, x0, x1) for y, (x0, x1) in found.items())


def _bands(rules: list[tuple[float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Group aligned, nearby rules into candidate table regions.

    Returns (x0, y_top, x1, y_bottom). Two rules are the minimum: a booktabs
    table with no midrule still has a toprule and a bottomrule.
    """
    out: list[tuple[float, float, float, float]] = []
    index = 0
    while index < len(rules):
        group = [rules[index]]
        cursor = index + 1
        while cursor < len(rules):
            y, x0, x1 = rules[cursor]
            prev_y, prev_x0, prev_x1 = group[-1]
            overlap = min(x1, prev_x1) - max(x0, prev_x0)
            widest = max(x1 - x0, prev_x1 - prev_x0)
            if y - prev_y <= _MAX_RULE_GAP and overlap > _RULE_ALIGNMENT * widest:
                group.append(rules[cursor])
                cursor += 1
            else:
                break
        if len(group) >= 2:
            out.append((
                min(g[1] for g in group), group[0][0],
                max(g[2] for g in group), group[-1][0],
            ))
        index = cursor if cursor > index + 1 else index + 1
    return out


def _rows(words: list[tuple]) -> list[tuple[float, list[tuple]]]:
    """Cluster words into rows by vertical centre.

    Subscripts and superscripts sit off the baseline, so clustering on the
    centre with a tolerance proportional to glyph height keeps "d_model" in one
    row where a baseline comparison would split it.
    """
    heights = [w[3] - w[1] for w in words] or [10.0]
    tolerance = statistics.median(heights) * _ROW_TOLERANCE
    rows: list[list[Any]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (word[1] + word[3]) / 2
        if rows and abs(centre - rows[-1][0]) <= tolerance:
            bucket = rows[-1]
            bucket[1].append(word)
            bucket[0] = (bucket[0] * (len(bucket[1]) - 1) + centre) / len(bucket[1])
        else:
            rows.append([centre, [word]])
    return [(r[0], sorted(r[1], key=lambda w: w[0])) for r in rows]


def _column_bounds(
    rows: list[tuple[float, list[tuple]]], x0: float, x1: float
) -> list[float]:
    """Split points between columns, found as vertical corridors of whitespace."""
    if not rows:
        return [x0, x1]
    char_widths = [
        (w[2] - w[0]) / max(1, len(w[4])) for _, row in rows for w in row if w[4].strip()
    ]
    char_width = statistics.median(char_widths) if char_widths else 4.0
    min_gap = max(1.5, char_width * _MIN_GAP_CHARS)

    resolution = _PROJECTION_RESOLUTION
    buckets = int((x1 - x0) / resolution) + 1
    coverage = [0] * buckets
    for _, row in rows:
        marked = bytearray(buckets)
        for word in row:
            start = max(0, int((word[0] - x0) / resolution))
            end = min(buckets - 1, int((word[2] - x0) / resolution))
            for position in range(start, end + 1):
                marked[position] = 1
        for position in range(buckets):
            if marked[position]:
                coverage[position] += 1

    # A corridor may be crossed by a stray row, a group heading for instance,
    # without ceasing to be a column boundary, so the test is "empty in nearly
    # every row" rather than "empty in every row".
    threshold = max(0, int(len(rows) * _EMPTY_ROW_FRACTION))
    gaps: list[tuple[float, float]] = []
    run: int | None = None
    for position in range(buckets):
        if coverage[position] <= threshold:
            if run is None:
                run = position
        else:
            if run is not None and (position - run) * resolution >= min_gap:
                gaps.append((x0 + run * resolution, x0 + position * resolution))
            run = None
    interior = [g for g in gaps if g[0] > x0 + 1 and g[1] < x1 - 1]
    return [x0] + [(a + b) / 2 for a, b in interior] + [x1]


def _median_gap(centres: list[float]) -> float:
    gaps = sorted(centres[i + 1] - centres[i] for i in range(len(centres) - 1))
    return statistics.median(gaps) if gaps else 0.0


def _trim_outliers(
    grid: list[list[str]], centres: list[float], header_floor: float = 0.0
) -> None:
    """Drop leading and trailing rows that sit far from the table body.

    A section heading just above the toprule, or a caption just below the
    bottomrule, falls inside the band but is separated from the table by much
    more than the table's own row pitch.

    `header_floor` is the y of the first midrule. Rows above it are the header,
    which in a multi level header is deliberately set with extra leading and so
    looks exactly like an outlier by pitch alone. Trimming it loses the column
    names while keeping every number, which is the worst of both: the table
    reads as though its first data row were the header. Table 2 of the
    Transformer paper is the case that exposed this.
    """
    while len(grid) > 2:
        pitch = _median_gap(centres)
        if pitch <= 0:
            return
        protected = header_floor > 0 and centres[0] < header_floor
        if not protected and centres[1] - centres[0] > pitch * _OUTLIER_GAP:
            grid.pop(0)
            centres.pop(0)
            continue
        if centres[-1] - centres[-2] > pitch * _OUTLIER_GAP:
            grid.pop()
            centres.pop()
            continue
        return


def _merge_wrapped(grid: list[list[str]], centres: list[float]) -> list[list[str]]:
    """Fold a wrapped header line into the row above it.

    Calibrated against the table's own median row pitch rather than a fixed
    multiple of font size, because what counts as tight differs per table. A
    first attempt used 1.45 times the glyph height, which is looser than normal
    line spacing, and collapsed a twenty one row table into two rows.
    """
    if len(centres) < 3:
        return grid
    pitch = _median_gap(centres)
    if pitch <= 0:
        return grid
    out: list[list[str]] = []
    out_centres: list[float] = []
    for index, row in enumerate(grid):
        if out and (centres[index] - out_centres[-1]) < pitch * _WRAP_GAP:
            previous = out[-1]
            # Only a continuation of cells already begun above, so a genuine
            # data row that happens to sit close is never absorbed.
            if any(row) and all((not cell) or previous[i] for i, cell in enumerate(row)):
                out[-1] = [
                    (f"{a} {b}".strip() if b else a) for a, b in zip(previous, row)
                ]
                out_centres[-1] = centres[index]
                continue
        out.append(list(row))
        out_centres.append(centres[index])
    centres[:] = out_centres
    return out


def _is_prose_row(cells: list[str]) -> bool:
    filled = [c for c in cells if c]
    return len(filled) == 1 and len(filled[0]) > 70


def build_grid(page, rect, header_floor: float = 0.0) -> tuple[list[list[str]], float] | None:
    """Extract a grid from one band, with a confidence between 0 and 1."""
    try:
        words = [w for w in page.get_text("words", clip=rect, sort=True) if w[4].strip()]
    except Exception:  # noqa: BLE001 - an unreadable region yields no table
        return None
    if len(words) < 6:
        return None

    rows = _rows(words)
    if len(rows) < 2:
        return None

    bounds = _column_bounds(rows, rect.x0, rect.x1)
    column_count = len(bounds) - 1
    if column_count < 2:
        return None

    grid: list[list[str]] = []
    centres: list[float] = []
    splits = bounds[1:-1]
    for centre, row in rows:
        cells = [""] * column_count
        for word in row:
            middle = (word[0] + word[2]) / 2
            index = min(column_count - 1, sum(1 for b in splits if middle >= b))
            # Through the same cleaner as prose, so a narrow no-break space
            # between a number and its unit does not survive into a cell and
            # come back out as "7.22?seconds".
            cells[index] = clean_pdf_text(cells[index] + " " + word[4])
        grid.append(cells)
        centres.append(centre)

    _trim_outliers(grid, centres, header_floor)
    grid = _merge_wrapped(grid, centres)
    while grid and _is_prose_row(grid[0]):
        grid.pop(0)
        centres.pop(0)
    while grid and _is_prose_row(grid[-1]):
        grid.pop()
        centres.pop()
    grid = [row for row in grid if any(row)]
    if len(grid) < 2:
        return None

    return grid, _confidence(grid)


def _confidence(grid: list[list[str]]) -> float:
    """How much to trust this grid, from shape alone.

    A real table is mostly filled, has short cells and usually carries numbers.
    Prose that slipped through has long cells and no numbers. The score is
    shown in the UI and decides whether the table is offered to a model as a
    grid or only as a rendered image.
    """
    cells = [c for row in grid for c in row if c]
    if not cells:
        return 0.0
    total = len(grid) * len(grid[0])
    fill = len(cells) / total if total else 0.0
    average_length = sum(len(c) for c in cells) / len(cells)
    numeric = sum(1 for c in cells if any(ch.isdigit() for ch in c)) / len(cells)

    score = 0.0
    score += min(1.0, fill / 0.6) * 0.35
    # Cells longer than about forty characters are sentences, not values.
    score += max(0.0, min(1.0, (45.0 - average_length) / 35.0)) * 0.4
    score += min(1.0, numeric / 0.3) * 0.25
    return round(max(0.0, min(1.0, score)), 2)


def find_tables(page) -> list[dict[str, Any]]:
    """Every table on one page, as {bbox, grid, confidence}."""
    import pymupdf

    rules = _horizontal_rules(page)
    out: list[dict[str, Any]] = []
    for x0, y_top, x1, y_bottom in _bands(rules):
        if y_bottom - y_top < 12 or x1 - x0 < 40:
            continue
        rect = pymupdf.Rect(x0 - 3, y_top - 2, x1 + 3, y_bottom + 2)
        # The first rule strictly inside the band is the midrule under the
        # header, so anything above it is a header row.
        inner = [y for y, _, _ in rules if y_top < y < y_bottom]
        built = build_grid(page, rect, inner[0] if inner else 0.0)
        if not built:
            continue
        grid, confidence = built
        out.append({
            "bbox": (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
            "grid": grid,
            "confidence": confidence,
        })
    return out


def table_bands(page) -> list[tuple[float, float, float, float]]:
    """Candidate table regions on a page, as (x0, y_top, x1, y_bottom).

    Exposed separately from `find_tables` because the text grouping pass needs
    the geometry before any cell has been extracted.
    """
    return [
        (x0, y_top, x1, y_bottom)
        for x0, y_top, x1, y_bottom in _bands(_horizontal_rules(page))
        if y_bottom - y_top >= 12 and x1 - x0 >= 40
    ]
