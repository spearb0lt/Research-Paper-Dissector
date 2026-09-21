"""Rendering a page of a PDF, on demand and cached.

The reader draws every extracted element as a box over the page it came from,
which is how someone checks a parse: a table whose columns are wrong, a figure
cropped badly, a heading that was missed, all become obvious the moment the
boxes are drawn. That needs a picture of the page.

Originally pages were rendered only by the OCR path, on the reasoning that
rendering every page of every paper would multiply storage for something rarely
looked at. That reasoning was wrong, and measurably so. On a 26 page paper
whose PDF is 8.3 MB:

    scale   per page    per page    whole paper
    1.0     28 ms       122 KB      3.2 MB
    1.5     38 ms       227 KB      6.0 MB
    2.0     58 ms       334 KB      8.9 MB

A page costs less than a fortieth of a second and the whole paper costs less
than the PDF already sitting in the blob store. The real conclusion is not that
rendering is expensive, it is that rendering eagerly is unnecessary: a page is
rendered the first time someone opens it and is content addressed thereafter,
so a paper nobody reads costs nothing and a page read twice is rendered once.
"""
from __future__ import annotations

import threading
from typing import Any

from . import blobs, settings
from .db import repo

# One render at a time per page. Two requests for the same page arriving
# together would otherwise both pay the cost, and on a long paper being scrolled
# quickly that is most of them.
_locks: dict[tuple[int, int], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(paper_id: int, page: int) -> threading.Lock:
    key = (paper_id, page)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def render(paper_id: int, page: int, *, scale: float | None = None) -> str:
    """Return the blob digest for a page image, rendering it if needed.

    The digest is recorded back onto the paper's stored page list, so a second
    request answers from the database without opening the PDF at all.
    """
    paper = repo.get_paper(paper_id)
    if paper is None:
        raise ValueError(f"No paper with id {paper_id}.")

    pages: list[dict[str, Any]] = list(paper.get("pages") or [])
    entry = next((p for p in pages if int(p.get("number", 0)) == page), None)
    if entry is None:
        raise ValueError(f"Paper {paper_id} has no page {page}.")

    existing = str(entry.get("render_digest") or "")
    if existing and blobs.exists(existing, "image/png"):
        return existing

    with _lock_for(paper_id, page):
        # Re-read after taking the lock: whoever held it may have done the work.
        paper = repo.get_paper(paper_id)
        pages = list((paper or {}).get("pages") or [])
        entry = next((p for p in pages if int(p.get("number", 0)) == page), None)
        if entry is None:
            raise ValueError(f"Paper {paper_id} has no page {page}.")
        existing = str(entry.get("render_digest") or "")
        if existing and blobs.exists(existing, "image/png"):
            return existing

        pdf_path = blobs.path_of(str(paper.get("blob_digest") or ""), "application/pdf")
        if pdf_path is None:
            raise ValueError(
                "The PDF for this paper is not in storage, so its pages cannot "
                "be rendered."
            )

        from .parse.fast import _fitz

        fitz = _fitz()
        book = fitz.open(str(pdf_path))
        try:
            if not 1 <= page <= book.page_count:
                raise ValueError(f"Page {page} is outside this document.")
            factor = float(scale or settings.PAGE_RENDER_SCALE)
            pixmap = book[page - 1].get_pixmap(
                matrix=fitz.Matrix(factor, factor), alpha=False
            )
            digest = blobs.put(pixmap.tobytes("png"), "image/png")
        finally:
            book.close()

        entry["render_digest"] = digest
        repo.update_paper(paper_id, pages=pages)
        return digest


def prerender(paper_id: int, *, limit: int = 0, progress=None) -> int:
    """Render every page up front. Only for a caller that wants it warm.

    Not on the ingest path: the whole point of rendering lazily is that a paper
    nobody opens costs nothing.
    """
    paper = repo.get_paper(paper_id)
    if paper is None:
        return 0
    count = int(paper.get("num_pages") or 0)
    if limit:
        count = min(count, limit)
    done = 0
    for number in range(1, count + 1):
        try:
            render(paper_id, number)
            done += 1
        except Exception:  # noqa: BLE001 - one bad page is not worth failing over
            continue
        if progress:
            progress(done, count)
    return done
