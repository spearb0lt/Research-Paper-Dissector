"""Routes added after the first cut: fetching, references, triage and export.

Kept in their own module rather than growing `routes.py` past the point where
anyone can find anything in it. Mounted on the same router prefix, so the split
is invisible from outside.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import blobs, export, fetch, refs, settings
from ..ask import answer as answer_module
from ..ask import lenses as lens_module
from ..db import repo
from ..parse.base import ParseError

router = APIRouter()


# ------------------------------------------------------------------ fetching


class FetchIn(BaseModel):
    source: str = Field(min_length=3, max_length=2000)
    collection_id: int | None = None
    parse_mode: str | None = None
    dense: bool | None = None
    ocr: bool | None = None


@router.post("/papers/fetch")
def fetch_paper(body: FetchIn) -> dict[str, Any]:
    """Add a paper from an arXiv id, a DOI or a URL instead of a file."""
    from .. import pipeline

    try:
        got = fetch.fetch(body.source)
    except fetch.FetchError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

    try:
        result = pipeline.ingest(
            got.data, got.filename,
            collection_id=body.collection_id,
            source_url=got.source_url,
            parse_mode=body.parse_mode,
            dense=body.dense,
            ocr=body.ocr,
        )
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

    # The identifier is more reliable than anything read off the first page.
    updates: dict[str, Any] = {}
    if got.arxiv_id:
        updates["arxiv_id"] = got.arxiv_id
    if got.doi:
        updates["doi"] = got.doi
    if updates:
        repo.update_paper(result.paper_id, **updates)

    return {"result": result.as_dict(), "paper": repo.get_paper(result.paper_id)}


@router.post("/papers/fetch/identify")
def identify_source(body: FetchIn) -> dict[str, Any]:
    """What a pasted string is, without downloading it. For the upload box."""
    try:
        return {"identified": fetch.identify(body.source)}
    except fetch.FetchError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


# ---------------------------------------------------------------- references


@router.get("/papers/{paper_id}/references")
def list_references(
    paper_id: int,
    resolve: bool = Query(False, description="Look up titles against arXiv and Crossref"),
    limit: int = Query(300, ge=1, le=1000),
) -> dict[str, Any]:
    """The reference list, with somewhere to view each entry and maybe fetch it.

    Resolution is off by default and explicit, because it makes one network
    request per unresolved entry and a reference list is long. What the entry
    itself prints, an arXiv id or a DOI, is always resolved, since that costs
    nothing.
    """
    elements = repo.list_elements(paper_id, kinds=["reference"], limit=limit)
    out: list[dict[str, Any]] = []
    for element in elements:
        extra = element.get("extra") or {}
        found = refs.resolve(extra, online=resolve)
        out.append({
            "element_id": int(element["id"]),
            "number": extra.get("number"),
            "text": element.get("text", ""),
            "page": element.get("page", 0),
            "title": extra.get("title", ""),
            "authors": extra.get("authors") or [],
            "year": extra.get("year"),
            "resolved": found.as_dict() if found else None,
            # Only offer Add when there is actually a PDF to add.
            "can_add": bool(found and found.pdf_url),
            "in_library": _already_here(found),
        })
    return {"references": out, "resolved": resolve}


def _already_here(found: refs.Resolved | None) -> int | None:
    """The id of this work if it is already in the library, so Add can say so."""
    if found is None:
        return None
    for paper in repo.list_papers(limit=500):
        if found.arxiv_id and str(paper.get("arxiv_id") or "") == found.arxiv_id:
            return int(paper["id"])
        if found.doi and str(paper.get("doi") or "").lower() == found.doi.lower():
            return int(paper["id"])
        if found.title and paper.get("title"):
            if refs.title_similarity(found.title, str(paper["title"])) > 0.9:
                return int(paper["id"])
    return None


class AddReferenceIn(BaseModel):
    collection_id: int | None = None
    parse_mode: str | None = None
    dense: bool | None = None


@router.post("/papers/{paper_id}/references/{element_id}/add")
def add_reference(paper_id: int, element_id: int, body: AddReferenceIn) -> dict[str, Any]:
    """Fetch and ingest the paper one reference points at.

    Separate from viewing on purpose: you look first, and only add what you
    decide you want.
    """
    from .. import pipeline

    elements = repo.get_elements_by_ids(paper_id, [element_id])
    if not elements:
        raise HTTPException(status_code=404, detail="No such reference.")

    found = refs.resolve(elements[0].get("extra") or {}, online=True)
    if found is None or not found.pdf_url:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "No freely downloadable PDF was found for that reference.",
                "hint": (
                    f"Open {found.view_url} and upload the PDF."
                    if found and found.view_url
                    else "Search for it and upload the PDF."
                ),
            },
        )

    try:
        got = fetch.fetch(found.pdf_url)
        result = pipeline.ingest(
            got.data, got.filename,
            collection_id=body.collection_id,
            source_url=found.view_url or got.source_url,
            parse_mode=body.parse_mode,
            dense=body.dense,
        )
    except (fetch.FetchError, ParseError) as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

    updates: dict[str, Any] = {}
    if found.arxiv_id:
        updates["arxiv_id"] = found.arxiv_id
    if found.doi:
        updates["doi"] = found.doi
    if updates:
        repo.update_paper(result.paper_id, **updates)

    return {"result": result.as_dict(), "paper": repo.get_paper(result.paper_id)}


# -------------------------------------------------------------------- triage


class TriageIn(BaseModel):
    read_state: str | None = None
    verdict: str | None = Field(None, max_length=600)


@router.patch("/papers/{paper_id}/triage")
def set_triage(paper_id: int, body: TriageIn) -> dict[str, Any]:
    if repo.get_paper(paper_id) is None:
        raise HTTPException(status_code=404, detail="No such paper.")
    try:
        repo.set_triage(paper_id, read_state=body.read_state, verdict=body.verdict)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"paper": repo.get_paper(paper_id)}


@router.get("/papers/{paper_id}/quality")
def get_quality(paper_id: int) -> dict[str, Any]:
    """Re-assess the parse from the stored rows, rather than the cached score."""
    from ..parse.quality import assess
    from ..runtime import current as runtime

    paper = repo.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="No such paper.")
    quality = assess(
        repo.list_elements(paper_id),
        list(paper.get("pages") or []),
        parser=str(paper.get("parser") or ""),
        deep_available=runtime().can("deep_parse"),
    )
    repo.update_paper(paper_id, quality=quality.as_dict())
    return {"quality": quality.as_dict()}


# -------------------------------------------------------------------- export


@router.get("/papers/{paper_id}/export.md", response_class=PlainTextResponse)
def export_markdown(paper_id: int) -> PlainTextResponse:
    paper = repo.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="No such paper.")
    analyses = [
        repo.get_analysis(paper_id, row["lens"]) or {}
        for row in repo.list_analyses(paper_id)
    ]
    text = export.analysis_markdown(paper, [a for a in analyses if a])
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers=_attachment(f"{paper_id}-analysis.md"),
    )


@router.get("/papers/{paper_id}/export.bib", response_class=PlainTextResponse)
def export_bibtex(paper_id: int) -> PlainTextResponse:
    paper = repo.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="No such paper.")
    return PlainTextResponse(
        export.bibtex(paper),
        media_type="application/x-bibtex; charset=utf-8",
        headers=_attachment(f"{paper_id}.bib"),
    )


@router.get("/papers/{paper_id}/elements/{element_id}/csv", response_class=PlainTextResponse)
def export_table(paper_id: int, element_id: int) -> PlainTextResponse:
    paper = repo.get_paper(paper_id)
    elements = repo.get_elements_by_ids(paper_id, [element_id])
    if paper is None or not elements:
        raise HTTPException(status_code=404, detail="No such table.")
    element = elements[0]
    if not element.get("table"):
        raise HTTPException(status_code=400, detail="That element is not a table.")
    return PlainTextResponse(
        export.table_csv(element),
        media_type="text/csv; charset=utf-8",
        headers=_attachment(export.table_filename(paper, element)),
    )


@router.get("/library/export.md", response_class=PlainTextResponse)
def export_library(collection_id: int | None = None) -> PlainTextResponse:
    papers = repo.list_papers(collection_id, limit=500)
    return PlainTextResponse(
        export.library_markdown(papers),
        media_type="text/markdown; charset=utf-8",
        headers=_attachment("reading-list.md"),
    )


@router.get("/papers/{paper_id}/figure/{element_id}")
def download_figure(paper_id: int, element_id: int) -> Response:
    """A figure at full resolution, named after the paper and the figure."""
    paper = repo.get_paper(paper_id)
    elements = repo.get_elements_by_ids(paper_id, [element_id])
    if paper is None or not elements:
        raise HTTPException(status_code=404, detail="No such figure.")
    element = elements[0]
    digest = (element.get("image") or {}).get("digest") or ""
    data = blobs.get(digest, "image/png") if digest else None
    if data is None:
        raise HTTPException(status_code=404, detail="That element has no image.")
    return Response(
        content=data,
        media_type="image/png",
        headers={
            **_attachment(export.figure_filename(paper, element)),
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _attachment(filename: str) -> dict[str, str]:
    # Quoted and stripped of anything that could break out of the header. A
    # filename comes from a paper title, which can contain anything at all.
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "-_. ")[:120]
    return {"Content-Disposition": f'attachment; filename="{safe or "download"}"'}


# ----------------------------------------------------------------- streaming


class AskStreamIn(BaseModel):
    paper_ids: list[int] = Field(default_factory=list)
    question: str = ""
    lens: str = ""
    thread_id: int | None = None
    use_dense: bool = True
    use_rerank: bool = False
    top_k: int | None = Field(None, ge=1, le=50)
    provider: str | None = None
    model: str | None = None
    embedding_provider: str | None = None


@router.post("/ask/stream")
async def ask_streaming(body: AskStreamIn) -> StreamingResponse:
    """Answer as server sent events, so text appears as it is written.

    The evidence is sent first, as one event, because it is ready the moment
    retrieval finishes and is useful on its own. Then the answer arrives in
    pieces, and a final event carries the validated citation list, which cannot
    be known until the whole answer exists.
    """
    if not body.paper_ids:
        raise HTTPException(status_code=400, detail="Name at least one paper.")
    if not body.question.strip() and not body.lens:
        raise HTTPException(status_code=400, detail="Ask a question or pick a lens.")

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        def work() -> dict[str, Any]:
            return answer_module.ask_streaming(
                body.paper_ids, body.question,
                lens=body.lens,
                history=_history(body.thread_id),
                provider=body.provider, model=body.model,
                use_dense=body.use_dense, use_rerank=body.use_rerank,
                top_k=body.top_k, embedding_provider=body.embedding_provider,
                emit=emit,
            )

        task = loop.run_in_executor(None, work)
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, pending = await asyncio.wait(
                [task, getter], return_when=asyncio.FIRST_COMPLETED
            )
            if getter in done:
                yield _sse(getter.result())
            else:
                getter.cancel()
            while not queue.empty():
                yield _sse(queue.get_nowait())
            if task.done():
                while not queue.empty():
                    yield _sse(queue.get_nowait())
                final = await task
                if body.thread_id:
                    _persist(body.thread_id, body.question, body.lens, final)
                yield _sse({"type": "done", **final})
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


def _history(thread_id: int | None) -> list[dict[str, Any]]:
    if not thread_id:
        return []
    return [
        {"role": m["role"], "content": m["content"]}
        for m in repo.list_messages(thread_id)
    ]


def _persist(thread_id: int, question: str, lens: str, final: dict[str, Any]) -> None:
    try:
        repo.add_message(thread_id, role="user", content=question, lens=lens)
        repo.add_message(
            thread_id, role="assistant",
            content=str(final.get("text") or ""),
            citations=final.get("citations") or [],
            lens=lens,
            provider=str(final.get("provider") or ""),
            model=str(final.get("model") or ""),
            usage=final.get("usage") or {},
        )
    except Exception:  # noqa: BLE001 - a failed save must not lose the answer
        pass


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/lenses/comparison")
def comparison_lenses() -> dict[str, Any]:
    return {"groups": lens_module.catalogue(comparison=True)}


# --------------------------------------------------------------------- notes


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    element_id: int | None = None
    colour: str = "yellow"


class NoteUpdateIn(BaseModel):
    body: str | None = Field(None, max_length=4000)
    colour: str | None = None


@router.get("/papers/{paper_id}/notes")
def list_notes(paper_id: int) -> dict[str, Any]:
    return {
        "notes": repo.list_notes(paper_id),
        "counts": repo.note_counts(paper_id),
    }


@router.post("/papers/{paper_id}/notes")
def create_note(paper_id: int, body: NoteIn) -> dict[str, Any]:
    if repo.get_paper(paper_id) is None:
        raise HTTPException(status_code=404, detail="No such paper.")
    if body.element_id is not None:
        if not repo.get_elements_by_ids(paper_id, [body.element_id]):
            raise HTTPException(
                status_code=404, detail="That element is not in this paper."
            )
    try:
        note = repo.create_note(
            paper_id, body=body.body, element_id=body.element_id, colour=body.colour
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"note": note}


@router.patch("/notes/{note_id}")
def update_note(note_id: int, body: NoteUpdateIn) -> dict[str, Any]:
    note = repo.update_note(note_id, body=body.body, colour=body.colour)
    if note is None:
        raise HTTPException(status_code=404, detail="No such note.")
    return {"note": note}


@router.delete("/notes/{note_id}")
def delete_note(note_id: int) -> dict[str, Any]:
    repo.delete_note(note_id)
    return {"ok": True}


@router.get("/notes")
def all_notes(limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    """Every note across the library, for the export and a future overview."""
    return {"notes": repo.list_notes(None, limit=limit)}


# ---------------------------------------------------------------- batch lens


class BatchLensIn(BaseModel):
    paper_ids: list[int] = Field(default_factory=list)
    lens: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    refresh: bool = False


@router.post("/lenses/batch")
async def batch_lens(body: BatchLensIn) -> StreamingResponse:
    """Run one lens over many papers, streaming each result as it lands.

    Sequential rather than concurrent on purpose. Every provider with a free
    tier rate limits per minute, and firing ten requests at once is the
    reliable way to get nine of them rejected. One at a time with each result
    streamed as it finishes is slower in the best case and far faster in the
    common one.

    Cached results are returned instantly and marked as cached, so re-running
    a lens over a library where most papers already have it costs only the new
    ones.
    """
    if not body.paper_ids:
        raise HTTPException(status_code=400, detail="Name at least one paper.")
    if lens_module.get(body.lens) is None:
        raise HTTPException(status_code=404, detail="No such lens.")
    if lens_module.is_comparison(body.lens):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "A comparison lens already reads every paper at once.",
                "hint": "Use /ask with the comparison lens instead of running it per paper.",
            },
        )

    async def stream():
        loop = asyncio.get_running_loop()
        total = len(body.paper_ids)

        for index, paper_id in enumerate(body.paper_ids, start=1):
            paper = repo.get_paper(int(paper_id))
            title = str((paper or {}).get("title") or f"Paper {paper_id}")
            yield _sse({
                "type": "progress", "index": index, "total": total,
                "paper_id": int(paper_id), "title": title,
            })

            if not body.refresh:
                cached = repo.get_analysis(int(paper_id), body.lens)
                if cached and cached.get("content"):
                    yield _sse({
                        "type": "result", "paper_id": int(paper_id), "title": title,
                        "cached": True, "analysis": cached,
                    })
                    continue

            def work(target: int = int(paper_id)) -> dict[str, Any]:
                return answer_module.ask(
                    [target], "", lens=body.lens,
                    provider=body.provider, model=body.model,
                ).as_dict()

            try:
                result = await loop.run_in_executor(None, work)
            except Exception as exc:  # noqa: BLE001 - one paper failing must not
                # stop the batch, which is the whole reason for running it.
                yield _sse({
                    "type": "failed", "paper_id": int(paper_id), "title": title,
                    "error": str(exc)[:300],
                })
                continue

            if result.get("evidence_only"):
                yield _sse({
                    "type": "failed", "paper_id": int(paper_id), "title": title,
                    "error": result.get("note") or "No answer was produced.",
                })
                continue

            saved = repo.save_analysis(
                int(paper_id), body.lens,
                content=str(result.get("text") or ""),
                citations=result.get("citations") or [],
                provider=str(result.get("provider") or ""),
                model=str(result.get("model") or ""),
                usage=result.get("usage") or {},
            )
            yield _sse({
                "type": "result", "paper_id": int(paper_id), "title": title,
                "cached": False, "analysis": saved,
            })

        yield _sse({"type": "done", "total": total})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
