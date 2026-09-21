"""Taking a PDF from bytes to a searchable paper.

Four stages, and the order they run in is the point: a paper becomes browsable
before it becomes searchable. Parsing is what produces figures, tables and the
outline, and it takes seconds. Indexing is what produces retrieval, and with
dense embeddings on it takes considerably longer. Committing the elements first
means the All Views screens light up while the index is still building, instead
of the whole paper sitting behind one spinner.

    store     write the PDF to the blob store, dedupe by content hash
    parse     elements, figures, tables, page geometry           -> browsable
    chunk     section aware chunks with deterministic context
    index     BM25 always, dense when enabled                    -> searchable

Every stage reports progress through a callback rather than logging, so the
same code drives a command line run and a server sent event stream to the
browser without either knowing about the other.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import blobs, settings
from .db import repo
from .embeddings.base import EmbeddingError
from .index import dense as dense_index
from .index import lexical
from .index.chunk import chunk_elements
from .parse import registry as parsers
from .parse.base import Element, Kind, ParseError, ParseResult
from .parse.ocr import run_ocr
from .runtime import current as runtime
from .util import file_hash

Progress = Callable[[str, str, float], None]


@dataclass
class IngestResult:
    paper_id: int
    created: bool
    reused: bool = False
    parse_seconds: float = 0.0
    index_seconds: float = 0.0
    element_count: int = 0
    chunk_count: int = 0
    dense_enabled: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "created": self.created,
            "reused": self.reused,
            "parse_seconds": round(self.parse_seconds, 2),
            "index_seconds": round(self.index_seconds, 2),
            "element_count": self.element_count,
            "chunk_count": self.chunk_count,
            "dense_enabled": self.dense_enabled,
            "warnings": self.warnings,
        }


def _noop(stage: str, message: str, fraction: float) -> None:
    return None


def ingest(
    data: bytes,
    filename: str,
    *,
    collection_id: int | None = None,
    source_url: str = "",
    parse_mode: str | None = None,
    dense: bool | None = None,
    ocr: bool | None = None,
    embedding_provider: str | None = None,
    chunk_tokens: int | None = None,
    chunk_overlap: int | None = None,
    force: bool = False,
    progress: Progress | None = None,
) -> IngestResult:
    """Ingest one PDF. Re-uploading the same bytes reuses the existing parse."""
    say = progress or _noop

    digest = file_hash(data)
    existing = repo.get_paper_by_hash(digest)
    if existing and not force and existing.get("status") == "ready":
        say("store", "This paper is already in the library", 1.0)
        return IngestResult(
            paper_id=int(existing["id"]),
            created=False,
            reused=True,
            element_count=sum((existing.get("counts") or {}).values()),
        )

    say("store", "Storing the file", 0.05)
    blob_digest = blobs.put(data, "application/pdf")
    if existing:
        paper_id = int(existing["id"])
        repo.update_paper(paper_id, filename=filename, blob_digest=blob_digest)
    else:
        paper = repo.create_paper(
            file_hash=digest,
            filename=filename,
            blob_digest=blob_digest,
            collection_id=collection_id,
            title=Path(filename).stem,
            source_url=source_url,
        )
        paper_id = int(paper["id"])

    pdf_path = blobs.path_of(blob_digest, "application/pdf")
    if pdf_path is None:
        repo.set_status(paper_id, "failed", "The stored file could not be read back.")
        raise ParseError(
            "The uploaded file could not be read back from storage.",
            hint=(
                "This deployment has no persistent disk, so uploads do not survive. "
                "Set DATA_DIR to a persistent volume, or run the container image."
            ) if not blobs.persistent() else "",
        )

    result = _parse(paper_id, str(pdf_path), parse_mode, ocr, say)
    _commit(paper_id, result, filename)

    index_started = time.monotonic()
    chunk_count, dense_on = _index(
        paper_id,
        result,
        dense=dense,
        embedding_provider=embedding_provider,
        chunk_tokens=chunk_tokens,
        chunk_overlap=chunk_overlap,
        say=say,
    )
    index_seconds = time.monotonic() - index_started

    repo.set_status(paper_id, "ready")
    say("done", "Ready", 1.0)
    return IngestResult(
        paper_id=paper_id,
        created=existing is None,
        parse_seconds=result.duration_seconds,
        index_seconds=index_seconds,
        element_count=len(result.elements),
        chunk_count=chunk_count,
        dense_enabled=dense_on,
        warnings=list(result.warnings),
    )


def _parse(
    paper_id: int,
    pdf_path: str,
    parse_mode: str | None,
    ocr: bool | None,
    say: Progress,
) -> ParseResult:
    repo.set_status(paper_id, "parsing")
    say("parse", "Reading the document", 0.1)

    try:
        result = parsers.parse(pdf_path, mode=parse_mode, log=lambda m: say("parse", m, 0.3))
    except ParseError as exc:
        repo.set_status(paper_id, "failed", exc.message)
        raise

    want_ocr = settings.OCR_ENABLED if ocr is None else ocr
    scanned = sum(1 for page in result.pages if page.needs_ocr)
    if scanned and not want_ocr and runtime().can("ocr"):
        result.warnings.append(
            f"{scanned} page(s) have no text layer and were not read. Turn OCR on "
            "and re-parse to extract their text."
        )
    if want_ocr:
        if runtime().can("ocr"):
            say("parse", "Running OCR over figures and scanned pages", 0.5)
            run_ocr(result, pdf_path, log=lambda m: say("parse", m, 0.55))
        else:
            result.warnings.append(f"OCR was requested but is unavailable: {runtime().reason('ocr')}")

    return result


def _commit(paper_id: int, result: ParseResult, filename: str) -> None:
    """Write the parse to the database, which is the point a paper is browsable."""
    payload = []
    for element in result.elements:
        as_dict = element.as_dict()
        as_dict["search_text"] = element.search_text()
        payload.append(as_dict)
    repo.replace_elements(paper_id, payload)

    meta = result.meta
    repo.update_paper(
        paper_id,
        title=meta.title or Path(filename).stem,
        title_source=meta.title_source,
        authors=meta.authors,
        abstract=meta.abstract,
        doi=meta.doi,
        arxiv_id=meta.arxiv_id,
        year=meta.year,
        venue=meta.venue,
        keywords=meta.keywords,
        num_pages=meta.num_pages,
        toc=meta.toc,
        pages=[p.as_dict() for p in result.pages],
        parser=result.parser,
        parser_version=result.parser_version,
        parse_seconds=result.duration_seconds,
        warnings=result.warnings,
        counts=result.counts(),
        quality=_quality(paper_id, result).as_dict(),
        status="indexing",
    )


def _quality(paper_id: int, result: ParseResult):
    """Score the parse, so a bad extraction is visible rather than silent."""
    from .parse.quality import assess

    return assess(
        [{**e.as_dict(), "extra": e.extra} for e in result.elements],
        [p.as_dict() for p in result.pages],
        parser=result.parser,
        deep_available=runtime().can("deep_parse"),
    )


def _index(
    paper_id: int,
    result: ParseResult,
    *,
    dense: bool | None,
    embedding_provider: str | None,
    chunk_tokens: int | None,
    chunk_overlap: int | None,
    say: Progress,
) -> tuple[int, bool]:
    say("index", "Building chunks", 0.6)
    chunks = chunk_elements(
        result.elements,
        paper_title=result.meta.title,
        max_tokens=chunk_tokens,
        overlap_tokens=chunk_overlap,
    )
    chunk_dicts = [c.as_dict() for c in chunks]
    chunk_ids = repo.replace_chunks(paper_id, chunk_dicts)

    # The chunks these analyses cited no longer exist under those ids.
    dropped = repo.delete_analyses(paper_id)
    if dropped:
        say("index", f"Cleared {dropped} cached analyses, the chunks they cited changed", 0.65)

    if not chunk_ids:
        repo.save_indexes(paper_id, lexical=None, dense=None)
        say("index", "This paper produced no text to index", 0.95)
        return 0, False

    say("index", f"Indexing {len(chunk_ids)} chunks", 0.7)
    # The lexical index reads the chunk text including its context prefix, so a
    # query naming a section matches chunks from that section even when the
    # chunk's own words never mention it.
    lexical_index = lexical.build(zip(chunk_ids, (c["text"] for c in chunk_dicts)))

    want_dense = settings.DENSE_INDEX_ENABLED if dense is None else dense
    dense_blob = None
    signature = ""
    if want_dense:
        try:
            say("index", "Embedding chunks locally", 0.75)
            built = dense_index.build(
                list(zip(chunk_ids, (c["text"] for c in chunk_dicts))),
                provider_id=embedding_provider,
                progress=lambda done, total: say(
                    "index", f"Embedded {done} of {total} chunks", 0.75 + 0.2 * done / max(1, total)
                ),
            )
            dense_blob = built.dumps()
            signature = built.signature()
        except EmbeddingError as exc:
            # A failed dense leg is a degraded search, not a failed ingest. BM25
            # alone still answers exact value questions, which is most of what
            # gets asked of a results table.
            want_dense = False
            result.warnings.append(
                f"Dense search is unavailable for this paper: {exc}. "
                "Keyword search still works."
            )
            repo.update_paper(paper_id, warnings=result.warnings)

    repo.save_indexes(
        paper_id,
        lexical=lexical_index.dumps(),
        dense=dense_blob,
        dense_signature=signature,
        chunk_tokens=chunk_tokens or settings.CHUNK_TOKENS,
        chunk_overlap=(chunk_overlap if chunk_overlap is not None else settings.CHUNK_OVERLAP),
    )
    return len(chunk_ids), bool(dense_blob)


def reindex(
    paper_id: int,
    *,
    dense: bool | None = None,
    embedding_provider: str | None = None,
    chunk_tokens: int | None = None,
    chunk_overlap: int | None = None,
    progress: Progress | None = None,
) -> tuple[int, bool]:
    """Rebuild chunks and indexes from the stored elements, without re-parsing.

    Changing the chunk size or switching embedding backend does not require
    reading the PDF again, and re-parsing a long paper to change one number
    would make the setting effectively unusable.
    """
    say = progress or _noop
    paper = repo.get_paper(paper_id)
    if paper is None:
        raise ValueError(f"No paper with id {paper_id}.")

    rows = repo.list_elements(paper_id)
    if not rows:
        raise ValueError("This paper has no stored elements. Re-upload it to parse again.")

    result = ParseResult(
        elements=[_element_from_row(r) for r in rows],
        meta=_meta_from_paper(paper),
        warnings=list(paper.get("warnings") or []),
    )
    repo.set_status(paper_id, "indexing")
    count, dense_on = _index(
        paper_id, result,
        dense=dense, embedding_provider=embedding_provider,
        chunk_tokens=chunk_tokens, chunk_overlap=chunk_overlap, say=say,
    )
    repo.set_status(paper_id, "ready")
    return count, dense_on


def _element_from_row(row: dict[str, Any]) -> Element:
    """Rebuild an Element from its stored row.

    Only the fields chunking reads are restored. The row itself remains the
    source of truth for display, so this deliberately does not round trip the
    bounding box or the image bytes.
    """
    from .parse.base import Image, Section, Table

    table = None
    if row.get("table"):
        raw = row["table"]
        table = Table(
            grid=raw.get("grid", []),
            html=raw.get("html", ""),
            markdown=raw.get("markdown", ""),
            num_rows=raw.get("num_rows", 0),
            num_cols=raw.get("num_cols", 0),
        )
    image = None
    if row.get("image"):
        raw = row["image"]
        image = Image(
            digest=raw.get("digest", ""),
            width=raw.get("width", 0),
            height=raw.get("height", 0),
            ocr_text=raw.get("ocr_text", ""),
            description=raw.get("description", ""),
        )
    try:
        kind = Kind(row["kind"])
    except ValueError:
        kind = Kind.OTHER
    try:
        section = Section(row.get("section") or "unknown")
    except ValueError:
        section = Section.UNKNOWN

    return Element(
        kind=kind,
        text=row.get("text", "") or "",
        page=int(row.get("page") or 0),
        order=int(row.get("ord") or 0),
        section=section,
        section_path=list(row.get("section_path") or []),
        level=int(row.get("level") or 0),
        label=row.get("label", "") or "",
        caption=row.get("caption", "") or "",
        table=table,
        image=image,
        linked_id=row.get("linked_id", "") or "",
        extra=row.get("extra") or {},
    )


def _meta_from_paper(paper: dict[str, Any]):
    from .parse.base import PaperMeta

    return PaperMeta(
        title=paper.get("title", "") or "",
        authors=list(paper.get("authors") or []),
        abstract=paper.get("abstract", "") or "",
        doi=paper.get("doi", "") or "",
        arxiv_id=paper.get("arxiv_id", "") or "",
        year=int(paper.get("year") or 0),
        num_pages=int(paper.get("num_pages") or 0),
    )
