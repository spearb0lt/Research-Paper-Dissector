"""How much to trust one parse.

The application already knows how well it did. The fast tier records whether it
inferred a table's columns or read them from ruling, whether a figure was
recovered as an embedded image or cropped from a guess, whether a page had a
text layer at all, and whether a caption was ever matched to the thing it
labels. None of that was ever shown, so a bad parse looked exactly like a good
one and a wrong number in a table looked as authoritative as a right one.

This turns those signals into a score and, more usefully, into a list of
specific concerns naming what is likely wrong and what to do about it. The
score is a summary; the concerns are the point.

Weighting reflects what actually misleads a reader. A table whose columns were
guessed is the most dangerous failure, because the numbers look fine and are in
the wrong columns. A page with no text layer is the most complete failure, but
it is at least obvious. A missing caption is untidy and harmless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Kind


@dataclass
class Concern:
    id: str
    severity: str          # high, medium, low
    message: str
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class Quality:
    score: float = 1.0
    grade: str = "good"     # good, fair, poor
    concerns: list[Concern] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "grade": self.grade,
            "concerns": [c.as_dict() for c in self.concerns],
            "stats": self.stats,
        }


def assess(
    elements: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    *,
    parser: str = "",
    deep_available: bool = False,
) -> Quality:
    """Score a stored parse. Works from rows, so it can run long after ingest."""
    quality = Quality()
    total_pages = len(pages) or 1

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for element in elements:
        by_kind.setdefault(str(element.get("kind")), []).append(element)

    tables = by_kind.get(Kind.TABLE.value, [])
    figures = by_kind.get(Kind.FIGURE.value, [])
    prose = by_kind.get(Kind.PARAGRAPH.value, [])
    scanned = [p for p in pages if p.get("needs_ocr")]
    read_by_ocr = any(
        (e.get("extra") or {}).get("source") == "ocr" for e in elements
    )

    inferred = [
        t for t in tables if (t.get("extra") or {}).get("structure_confident") is False
    ]
    cropped = [
        f for f in figures if (f.get("extra") or {}).get("source") == "rendered"
    ]
    uncaptioned = [f for f in figures if not (f.get("caption") or "").strip()]
    unclassified = [e for e in elements if e.get("section") in (None, "", "unknown")]

    quality.stats = {
        "pages": len(pages),
        "elements": len(elements),
        "tables": len(tables),
        "tables_inferred": len(inferred),
        "figures": len(figures),
        "figures_cropped": len(cropped),
        "figures_uncaptioned": len(uncaptioned),
        "formulas": len(by_kind.get(Kind.FORMULA.value, [])),
        "references": len(by_kind.get(Kind.REFERENCE.value, [])),
        "scanned_pages": len(scanned),
        "unclassified_fraction": round(len(unclassified) / max(1, len(elements)), 2),
        "parser": parser,
    }

    # A page with no text layer that was never OCR'd contributes nothing at all.
    if scanned and not read_by_ocr:
        share = len(scanned) / total_pages
        quality.score -= min(0.55, 0.15 + share * 0.6)
        quality.concerns.append(Concern(
            id="scanned_pages",
            severity="high" if share > 0.3 else "medium",
            message=(
                f"{len(scanned)} of {total_pages} pages have no text layer, so "
                "nothing on them was read."
            ),
            fix="Re-parse with OCR turned on.",
        ))

    # The dangerous one: numbers present, columns guessed.
    if inferred:
        share = len(inferred) / max(1, len(tables))
        quality.score -= min(0.3, 0.12 + share * 0.25)
        quality.concerns.append(Concern(
            id="inferred_tables",
            severity="high",
            message=(
                f"{len(inferred)} of {len(tables)} tables had their columns "
                "inferred from spacing rather than read from ruling lines."
            ),
            fix=(
                "Check those tables against the rendered image before relying "
                "on the numbers."
                + (" A deep parse reads them properly." if deep_available else "")
            ),
        ))

    if len(elements) < total_pages * 5:
        quality.score -= 0.2
        quality.concerns.append(Concern(
            id="sparse",
            severity="high",
            message=(
                f"Only {len(elements)} elements were extracted from "
                f"{total_pages} pages, which is far fewer than a typical paper."
            ),
            fix="The PDF may be a scan or use an unusual layout. Try a deep parse with OCR.",
        ))

    if not tables and not figures:
        quality.score -= 0.1
        quality.concerns.append(Concern(
            id="no_visuals",
            severity="low",
            message="No figures or tables were found.",
            fix="Some papers have none. If this one does, try a deep parse.",
        ))

    if cropped:
        quality.score -= min(0.12, 0.04 * len(cropped) / max(1, len(figures)) * 3)
        quality.concerns.append(Concern(
            id="cropped_figures",
            severity="low",
            message=(
                f"{len(cropped)} of {len(figures)} figures are drawn in vector "
                "primitives and were recovered by cropping the page, so the "
                "edges are approximate."
            ),
            fix="A deep parse extracts them properly." if deep_available else "",
        ))

    if figures and len(uncaptioned) / len(figures) > 0.5:
        quality.score -= 0.08
        quality.concerns.append(Concern(
            id="uncaptioned_figures",
            severity="low",
            message=f"{len(uncaptioned)} of {len(figures)} figures have no caption attached.",
            fix="Retrieval will find them by page and by any text inside them instead.",
        ))

    if elements and len(unclassified) / len(elements) > 0.6:
        quality.score -= 0.1
        quality.concerns.append(Concern(
            id="unclassified",
            severity="medium",
            message=(
                "Most elements could not be placed in a section, so section "
                "filters and the analysis lenses have less to work with."
            ),
            fix="The paper may not use conventional headings.",
        ))

    if prose and not by_kind.get(Kind.REFERENCE.value):
        quality.concerns.append(Concern(
            id="no_references",
            severity="low",
            message="No reference entries were recognised.",
            fix="In-text citations will not resolve to anything.",
        ))

    quality.score = max(0.0, min(1.0, quality.score))
    quality.grade = (
        "good" if quality.score >= 0.8 else "fair" if quality.score >= 0.55 else "poor"
    )
    return quality
