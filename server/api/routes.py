"""The HTTP surface.

Every route is thin. Anything with judgement in it lives in pipeline.py,
ask/ or index/, so that the command line and the API exercise the same code and
a bug found through one is fixed for both.

Two conventions run through all of it. A capability this deployment cannot
provide is reported as unavailable with a reason the user can act on, never as
a 500. And a request may carry its own provider keys, bound for the lifetime of
that request only, which is what lets this be deployed publicly with no keys of
its own.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import blobs, pages, settings
from ..ask import answer as answer_module
from ..ask import lenses as lens_module
from ..ask import retrieve as retrieve_module
from ..db import repo
from ..embeddings import registry as embeddings
from ..index import rerank
from ..llm import registry as llm
from ..parse import registry as parsers
from ..parse.base import ParseError
from ..runtime import current as runtime
from ..util import file_hash

router = APIRouter()


# ------------------------------------------------------------------- health


@router.get("/health")
def health() -> dict[str, Any]:
    checks: dict[str, Any] = {"api": True}
    try:
        repo.get_db().ensure_schema()
        repo.list_collections()
        checks["database"] = True
    except Exception as exc:  # noqa: BLE001 - the whole point is to report it
        checks["database"] = False
        checks["database_error"] = str(exc)[:300]
    return {
        "ok": all(v for v in checks.values() if isinstance(v, bool)),
        "app": settings.APP_NAME,
        "checks": checks,
        "runtime": runtime().as_dict(),
    }


@router.get("/config")
def config() -> dict[str, Any]:
    """Everything the UI needs to render its controls correctly on first paint."""
    return {
        "app_name": settings.APP_NAME,
        "tagline": settings.APP_TAGLINE,
        "allow_client_keys": settings.ALLOW_CLIENT_KEYS,
        "runtime": runtime().as_dict(),
        "parsers": parsers.all_status(),
        "providers": llm.all_status(),
        "embedders": embeddings.all_status(),
        "lenses": lens_module.catalogue(),
        "rerank": {
            "available": rerank.available(),
            "reason": rerank.unavailable_reason(),
            "model": settings.RERANK_MODEL,
        },
        "defaults": {
            "parse_mode": settings.PARSE_MODE,
            "chunk_tokens": settings.CHUNK_TOKENS,
            "chunk_overlap": settings.CHUNK_OVERLAP,
            "dense_index": settings.DENSE_INDEX_ENABLED,
            "ocr": settings.OCR_ENABLED,
            "top_k": settings.RETRIEVE_TOP_K,
            "embedding_provider": settings.EMBEDDING_PROVIDER,
        },
        "storage": {
            "persistent": blobs.persistent(),
            "reason": "" if blobs.persistent() else runtime().reason("persistent_disk"),
        },
    }


# -------------------------------------------------------------- collections


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


@router.get("/collections")
def list_collections() -> dict[str, Any]:
    return {"collections": repo.list_collections()}


@router.post("/collections")
def create_collection(body: CollectionIn) -> dict[str, Any]:
    return {"collection": dict(repo.create_collection(body.name, body.description))}


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: int) -> dict[str, Any]:
    repo.delete_collection(collection_id)
    return {"ok": True}


# ------------------------------------------------------------------ papers


@router.get("/papers")
def list_papers(
    collection_id: int | None = None,
    q: str = "",
    status: str = "",
    read_state: str = "",
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return {
        "papers": repo.list_papers(
            collection_id, query=q, status=status, read_state=read_state,
            limit=limit, offset=offset,
        )
    }


@router.get("/papers/{paper_id}")
def get_paper(paper_id: int) -> dict[str, Any]:
    paper = repo.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="No such paper.")
    return {
        "paper": paper,
        "element_counts": repo.element_counts(paper_id),
        "section_counts": repo.section_counts(paper_id),
    }


@router.delete("/papers/{paper_id}")
def delete_paper(paper_id: int) -> dict[str, Any]:
    repo.delete_paper(paper_id)
    return {"ok": True}


@router.post("/papers")
async def upload_paper(
    file: UploadFile = File(...),
    collection_id: int | None = Form(None),
    parse_mode: str | None = Form(None),
    dense: bool | None = Form(None),
    ocr: bool | None = Form(None),
    embedding_provider: str | None = Form(None),
    chunk_tokens: int | None = Form(None),
    chunk_overlap: int | None = Form(None),
    force: bool = Form(False),
) -> dict[str, Any]:
    """Upload and ingest one PDF, returning when it is ready.

    Synchronous on purpose. A serverless deployment cannot keep working after
    the response is written, so a background job would simply never finish
    there. The streaming variant below exists for the browser, which wants
    progress rather than a long silence.
    """
    data = await _read_upload(file)
    from .. import pipeline

    try:
        result = pipeline.ingest(
            data, file.filename or "paper.pdf",
            collection_id=collection_id, parse_mode=parse_mode, dense=dense,
            ocr=ocr, embedding_provider=embedding_provider,
            chunk_tokens=chunk_tokens, chunk_overlap=chunk_overlap, force=force,
        )
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    return {"result": result.as_dict(), "paper": repo.get_paper(result.paper_id)}


@router.post("/papers/stream")
async def upload_paper_streaming(
    file: UploadFile = File(...),
    collection_id: int | None = Form(None),
    parse_mode: str | None = Form(None),
    dense: bool | None = Form(None),
    ocr: bool | None = Form(None),
    embedding_provider: str | None = Form(None),
    force: bool = Form(False),
) -> StreamingResponse:
    """Ingest with progress, as server sent events.

    Parsing a long paper with the deep parser takes minutes, and a progress
    bar that moves is the difference between waiting and assuming it crashed.
    """
    data = await _read_upload(file)
    filename = file.filename or "paper.pdf"

    async def stream():
        from .. import pipeline

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(stage: str, message: str, fraction: float) -> None:
            # Called from the worker thread, so the put has to be marshalled
            # back onto the event loop rather than awaited here.
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "stage": stage, "message": message,
                 "fraction": round(fraction, 3)},
            )

        def work():
            try:
                result = pipeline.ingest(
                    data, filename, collection_id=collection_id,
                    parse_mode=parse_mode, dense=dense, ocr=ocr,
                    embedding_provider=embedding_provider, force=force,
                    progress=progress,
                )
                return {"type": "done", "result": result.as_dict(),
                        "paper": repo.get_paper(result.paper_id)}
            except ParseError as exc:
                return {"type": "error", "error": exc.to_dict()}
            except Exception as exc:  # noqa: BLE001 - surfaced to the browser
                return {"type": "error",
                        "error": {"message": str(exc)[:400], "hint": ""}}

        task = loop.run_in_executor(None, work)
        while True:
            done, _ = await asyncio.wait(
                [task, asyncio.ensure_future(queue.get())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            drained = False
            while not queue.empty():
                yield _sse(queue.get_nowait())
                drained = True
            if task.done():
                while not queue.empty():
                    yield _sse(queue.get_nowait())
                yield _sse(await task)
                return
            if not drained:
                await asyncio.sleep(0.05)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        # Without this an nginx or Render proxy buffers the whole stream and
        # delivers it at the end, which defeats the point entirely.
        "X-Accel-Buffering": "no",
    })


class ReindexIn(BaseModel):
    dense: bool | None = None
    embedding_provider: str | None = None
    chunk_tokens: int | None = Field(None, ge=64, le=4000)
    chunk_overlap: int | None = Field(None, ge=0, le=1000)


@router.post("/papers/{paper_id}/reindex")
def reindex_paper(paper_id: int, body: ReindexIn) -> dict[str, Any]:
    from .. import pipeline

    try:
        count, dense_on = pipeline.reindex(
            paper_id, dense=body.dense, embedding_provider=body.embedding_provider,
            chunk_tokens=body.chunk_tokens, chunk_overlap=body.chunk_overlap,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"chunk_count": count, "dense_enabled": dense_on,
            "paper": repo.get_paper(paper_id)}


# ---------------------------------------------------------------- elements


@router.get("/papers/{paper_id}/elements")
def list_elements(
    paper_id: int,
    kind: str = "",
    section: str = "",
    page: int | None = None,
    limit: int = Query(2000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """The All Views data source: every element, filterable by kind, section and page."""
    kinds = [k.strip() for k in kind.split(",") if k.strip()]
    sections = [s.strip() for s in section.split(",") if s.strip()]
    return {
        "elements": repo.list_elements(
            paper_id, kinds=kinds, sections=sections, page=page,
            limit=limit, offset=offset,
        ),
        "counts": repo.element_counts(paper_id),
        "sections": repo.section_counts(paper_id),
    }


@router.get("/papers/{paper_id}/outline")
def outline(paper_id: int) -> dict[str, Any]:
    """Headings in reading order, with what sits under each."""
    headings = repo.list_elements(paper_id, kinds=["heading", "title"])
    counts: dict[str, int] = {}
    for element in repo.list_elements(paper_id):
        section = str(element.get("section") or "unknown")
        counts[section] = counts.get(section, 0) + 1
    return {"headings": headings, "section_counts": counts}


@router.get("/papers/{paper_id}/chunks")
def list_chunks(paper_id: int) -> dict[str, Any]:
    return {"chunks": repo.list_chunks(paper_id)}


@router.get("/papers/{paper_id}/page/{number}")
def page_image(paper_id: int, number: int, scale: float | None = None) -> Response:
    """The rendered image of one page, for the reader overlay.

    Rendered on first request and content addressed afterwards, so a paper
    nobody opens costs nothing and a page opened twice is rendered once.
    """
    if not 1 <= number <= 2000:
        raise HTTPException(status_code=400, detail="That page number is out of range.")
    try:
        digest = pages.render(paper_id, number, scale=scale)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

    data = blobs.get(digest, "image/png")
    if data is None:
        raise HTTPException(status_code=500, detail="The page render could not be read back.")
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            # So the client can address it by digest later without re-rendering.
            "X-Blob-Digest": digest,
        },
    )


@router.get("/blob/{digest}")
def get_blob(digest: str, kind: str = "image") -> Response:
    """Serve a stored figure, table render, page image or the PDF itself.

    The media type is chosen from a fixed map rather than from anything in the
    request, and the digest is validated as hex, so no request can reach a path
    outside the blob store or have a file served under a type of its choosing.
    """
    if not (len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
        raise HTTPException(status_code=400, detail="Not a valid digest.")
    media_type = "application/pdf" if kind == "pdf" else "image/png"
    data = blobs.get(digest, media_type)
    if data is None:
        raise HTTPException(status_code=404, detail="No such blob.")
    return Response(
        content=data,
        media_type=media_type,
        headers={
            # Content addressed, so it can never change under this URL.
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ------------------------------------------------------------------ search


@router.get("/search")
def search(
    paper_ids: str = Query(..., description="Comma separated paper ids"),
    q: str = Query(..., min_length=1),
    top_k: int = Query(12, ge=1, le=50),
    dense: bool = True,
    lexical: bool = True,
    rerank_hits: bool = False,
    kind: str = "",
    section: str = "",
) -> dict[str, Any]:
    """Ranked retrieval. This is the whole of no-LLM mode."""
    ids = _ids(paper_ids)
    found = retrieve_module.search(
        ids, q, top_k=top_k, use_dense=dense, use_lexical=lexical,
        use_rerank=rerank_hits,
        kinds=[k.strip() for k in kind.split(",") if k.strip()],
        sections=[s.strip() for s in section.split(",") if s.strip()],
    )
    return found.as_dict()


@router.get("/find")
def find(
    paper_ids: str = Query(...),
    q: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Every occurrence of a term, across text, tables, captions and figure OCR.

    Distinct from /search: exhaustive and literal rather than ranked, because
    a reader asking where a term appears wants all of them.
    """
    return retrieve_module.find_everywhere(_ids(paper_ids), q, limit=limit)


# ------------------------------------------------------------------- asking


class AskIn(BaseModel):
    paper_ids: list[int] = Field(default_factory=list)
    question: str = ""
    lens: str = ""
    thread_id: int | None = None
    use_llm: bool = True
    use_dense: bool = True
    use_rerank: bool = False
    top_k: int | None = Field(None, ge=1, le=50)
    provider: str | None = None
    model: str | None = None
    embedding_provider: str | None = None


@router.post("/ask")
def ask(body: AskIn) -> dict[str, Any]:
    if not body.paper_ids:
        raise HTTPException(status_code=400, detail="Name at least one paper.")
    if not body.question.strip() and not body.lens:
        raise HTTPException(status_code=400, detail="Ask a question or pick a lens.")

    history: list[dict[str, Any]] = []
    if body.thread_id:
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in repo.list_messages(body.thread_id)
        ]

    result = answer_module.ask(
        body.paper_ids, body.question, lens=body.lens, history=history,
        provider=body.provider, model=body.model, use_llm=body.use_llm,
        use_rerank=body.use_rerank, use_dense=body.use_dense, top_k=body.top_k,
        embedding_provider=body.embedding_provider,
    )

    if body.thread_id:
        repo.add_message(body.thread_id, role="user", content=body.question, lens=body.lens)
        repo.add_message(
            body.thread_id, role="assistant", content=result.text,
            citations=result.citations, lens=body.lens,
            provider=result.provider, model=result.model, usage=result.usage,
            retrieval={"legs_used": result.retrieval.legs_used if result.retrieval else [],
                       "rerank_used": result.retrieval.rerank_used if result.retrieval else False},
        )
    return result.as_dict()


@router.get("/lenses")
def list_lenses() -> dict[str, Any]:
    return {"groups": lens_module.catalogue()}


class LensIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    refresh: bool = False


@router.post("/papers/{paper_id}/lenses/{lens_id}")
def run_lens(paper_id: int, lens_id: str, body: LensIn) -> dict[str, Any]:
    """Run an analysis lens, reusing the stored result unless asked to refresh.

    Cached because a lens is expensive and deterministic enough that running it
    twice on an unchanged paper wastes a call to say the same thing.
    """
    if lens_module.get(lens_id) is None:
        raise HTTPException(status_code=404, detail="No such lens.")
    if not body.refresh:
        existing = repo.get_analysis(paper_id, lens_id)
        if existing and existing.get("content"):
            return {"analysis": existing, "cached": True}

    result = answer_module.ask(
        [paper_id], "", lens=lens_id, provider=body.provider, model=body.model
    )
    if result.evidence_only:
        return {"analysis": None, "cached": False, "note": result.note,
                "citations": result.citations}
    saved = repo.save_analysis(
        paper_id, lens_id, content=result.text, citations=result.citations,
        provider=result.provider, model=result.model, usage=result.usage,
    )
    return {"analysis": saved, "cached": False}


@router.get("/papers/{paper_id}/lenses")
def list_paper_lenses(paper_id: int) -> dict[str, Any]:
    return {"analyses": repo.list_analyses(paper_id)}


# ------------------------------------------------------------------ threads


class ThreadIn(BaseModel):
    collection_id: int | None = None
    paper_ids: list[int] = Field(default_factory=list)
    title: str = ""


@router.post("/threads")
def create_thread(body: ThreadIn) -> dict[str, Any]:
    return {"thread": repo.create_thread(
        collection_id=body.collection_id, paper_ids=body.paper_ids, title=body.title
    )}


@router.get("/threads")
def list_threads(collection_id: int | None = None, paper_id: int | None = None) -> dict[str, Any]:
    return {"threads": repo.list_threads(collection_id=collection_id, paper_id=paper_id)}


@router.get("/threads/{thread_id}")
def get_thread(thread_id: int) -> dict[str, Any]:
    thread = repo.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="No such thread.")
    return {"thread": thread, "messages": repo.list_messages(thread_id)}


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: int) -> dict[str, Any]:
    repo.delete_thread(thread_id)
    return {"ok": True}


# ------------------------------------------------------------------- usage


@router.get("/usage")
def usage(paper_id: int | None = None) -> dict[str, Any]:
    return {
        "totals": repo.usage_totals(paper_id=paper_id),
        "by_model": repo.usage_by_model(),
    }


@router.post("/providers/verify")
def verify_provider(request: Request, provider: str = Query(...)) -> dict[str, Any]:
    """Prove a key works, and report what it can call."""
    adapter = llm.provider_map().get(provider)
    if adapter is None:
        raise HTTPException(status_code=404, detail="No such provider.")
    try:
        models = adapter.verify()
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        detail = exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)[:300]}
        return {"ok": False, **detail}
    return {"ok": True, "models": models[:80]}


# ----------------------------------------------------------------- helpers


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is larger than the {settings.MAX_UPLOAD_BYTES // 1_000_000} MB limit.",
        )
    # Content sniffing rather than trusting the extension or the declared type,
    # both of which the client chooses.
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="That file is not a PDF.")
    return data


def _ids(raw: str) -> list[int]:
    try:
        ids = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="paper_ids must be integers.") from exc
    if not ids:
        raise HTTPException(status_code=400, detail="Name at least one paper.")
    return ids


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
