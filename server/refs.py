"""Turning an extracted reference into something you can open and fetch.

A reference entry as printed is a string. To offer a View button and an Add
button it has to become a URL to look at and a URL to download, and those come
from three sources, tried in that order because that is the order of certainty:

1. **What the entry already says.** A DOI or an arXiv id printed in the entry
   resolves with no lookup at all, which covers most of machine learning and a
   good deal of everything else.
2. **arXiv's search API**, by title. Free, no key, no registration.
3. **Crossref**, by title. Free, no key, and asks only that you identify
   yourself, which `USER_AGENT` does.

Nothing here is required for the application to work. A reference that resolves
to nothing is still listed, still searchable and still cited; it simply has no
buttons. That matters because these are third party services that will be slow
or down sometimes, and a reference list that fails to render because Crossref
had a bad minute would be a poor trade.

Results are cached in the element's own `extra`, so a list resolves once.
"""
from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Any

from . import settings
from .util import normalise_text

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"

# Both services ask for politeness rather than enforcing it. One request at a
# time with a gap between is well inside what either asks for, and a reference
# list is resolved once and then cached.
_MIN_INTERVAL = 0.34
_last_call = 0.0
_throttle = threading.Lock()

# A title match this close is the same work. Below it, the search found
# something else with similar words, which is worse than finding nothing
# because it attaches the wrong paper to a citation.
_TITLE_MATCH = 0.82


@dataclass
class Resolved:
    """What is known about a cited work, and where to get it."""

    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int = 0
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    # Where a human should look. A landing page, not a file.
    view_url: str = ""
    # Where the PDF is, when there is a free one. Empty means Add is not
    # offered, because offering a download that leads to a paywall is worse
    # than not offering it.
    pdf_url: str = ""
    source: str = ""          # printed, arxiv, crossref
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "view_url": self.view_url,
            "pdf_url": self.pdf_url,
            "source": self.source,
            "confidence": round(self.confidence, 2),
        }


def _wait() -> None:
    global _last_call
    with _throttle:
        gap = time.monotonic() - _last_call
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        _last_call = time.monotonic()


def _get(url: str, params: dict[str, Any]) -> Any:
    import requests

    _wait()
    response = requests.get(
        url,
        params=params,
        timeout=settings.FETCH_TIMEOUT,
        headers={"User-Agent": settings.USER_AGENT},
    )
    response.raise_for_status()
    return response


_WORD = re.compile(r"[a-z0-9]+")


def title_similarity(left: str, right: str) -> float:
    """Word overlap between two titles, ignoring order and punctuation.

    Jaccard rather than an edit distance: a bibliography routinely drops a
    subtitle or expands an abbreviation, which wrecks an edit distance and
    barely moves an overlap.
    """
    a = set(_WORD.findall(normalise_text(left)))
    b = set(_WORD.findall(normalise_text(right)))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def from_printed(extra: dict[str, Any]) -> Resolved | None:
    """Resolve from identifiers the entry itself carries. No network."""
    arxiv_id = str(extra.get("arxiv_id") or "").strip()
    doi = str(extra.get("doi") or "").strip()
    url = str(extra.get("url") or "").strip()

    if arxiv_id:
        return Resolved(
            title=str(extra.get("title") or ""),
            authors=list(extra.get("authors") or []),
            year=int(extra.get("year") or 0),
            arxiv_id=arxiv_id,
            view_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            source="printed",
            confidence=1.0,
        )
    if doi:
        return Resolved(
            title=str(extra.get("title") or ""),
            authors=list(extra.get("authors") or []),
            year=int(extra.get("year") or 0),
            doi=doi,
            view_url=f"https://doi.org/{doi}",
            # A DOI is not a PDF. Whether one is free is unknown from here, so
            # Add is not offered and the reader follows the link instead.
            pdf_url="",
            source="printed",
            confidence=0.95,
        )
    if url:
        return Resolved(
            title=str(extra.get("title") or ""),
            authors=list(extra.get("authors") or []),
            year=int(extra.get("year") or 0),
            view_url=url,
            pdf_url=url if url.lower().endswith(".pdf") else "",
            source="printed",
            confidence=0.8,
        )
    return None


def from_arxiv(title: str, *, year: int = 0) -> Resolved | None:
    """Search arXiv by title. Returns nothing rather than a weak guess."""
    if len(title) < 12:
        return None
    try:
        response = _get(ARXIV_API, {
            "search_query": f'ti:"{title[:200]}"',
            "max_results": 5,
            "sortBy": "relevance",
        })
        tree = ElementTree.fromstring(response.content)
    except Exception:  # noqa: BLE001 - a lookup that fails resolves to nothing
        return None

    namespace = {"a": "http://www.w3.org/2005/Atom"}
    best: Resolved | None = None
    for entry in tree.findall("a:entry", namespace):
        found_title = " ".join((entry.findtext("a:title", "", namespace) or "").split())
        score = title_similarity(title, found_title)
        if score < _TITLE_MATCH or (best and score <= best.confidence):
            continue
        identifier = (entry.findtext("a:id", "", namespace) or "").rstrip("/")
        arxiv_id = identifier.rsplit("/abs/", 1)[-1]
        published = entry.findtext("a:published", "", namespace) or ""
        best = Resolved(
            title=found_title,
            authors=[
                " ".join((a.findtext("a:name", "", namespace) or "").split())
                for a in entry.findall("a:author", namespace)
            ][:24],
            year=int(published[:4]) if published[:4].isdigit() else year,
            arxiv_id=arxiv_id,
            view_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            source="arxiv",
            confidence=score,
        )
    return best


def from_crossref(title: str, *, year: int = 0) -> Resolved | None:
    """Search Crossref by title, for anything that is not on arXiv."""
    if len(title) < 12:
        return None
    try:
        response = _get(CROSSREF_API, {
            "query.bibliographic": title[:300],
            "rows": 5,
            "select": "DOI,title,author,issued,container-title,link",
        })
        items = (response.json().get("message") or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return None

    best: Resolved | None = None
    for item in items:
        found_title = " ".join((item.get("title") or [""])[0].split())
        score = title_similarity(title, found_title)
        if score < _TITLE_MATCH or (best and score <= best.confidence):
            continue
        parts = ((item.get("issued") or {}).get("date-parts") or [[0]])[0]
        doi = str(item.get("DOI") or "")
        pdf_url = ""
        for link in item.get("link") or []:
            if str(link.get("content-type", "")).lower() == "application/pdf":
                pdf_url = str(link.get("URL") or "")
                break
        best = Resolved(
            title=found_title,
            authors=[
                " ".join(filter(None, [a.get("given"), a.get("family")]))
                for a in (item.get("author") or [])
            ][:24],
            year=int(parts[0]) if parts and str(parts[0]).isdigit() else year,
            venue=(item.get("container-title") or [""])[0],
            doi=doi,
            view_url=f"https://doi.org/{doi}" if doi else "",
            pdf_url=pdf_url,
            source="crossref",
            confidence=score,
        )
    return best


def resolve(extra: dict[str, Any], *, online: bool = True) -> Resolved | None:
    """Best available identity for one reference entry."""
    printed = from_printed(extra)
    if printed is not None and printed.arxiv_id:
        return printed
    if not online:
        return printed

    title = str(extra.get("title") or "").strip()
    year = int(extra.get("year") or 0)
    if title:
        found = from_arxiv(title, year=year) or from_crossref(title, year=year)
        if found is not None:
            # A printed DOI beats a searched one, so the two are merged rather
            # than the search simply winning.
            if printed is not None and printed.doi and not found.doi:
                found.doi = printed.doi
                found.view_url = found.view_url or printed.view_url
            return found
    return printed
