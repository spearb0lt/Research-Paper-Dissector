"""Turning retrieved evidence into a cited answer.

The grounding here is deliberately severe, because the failure mode this
application has to avoid is specific and serious. A tool that helps someone
decide whether a paper's findings are credible, and that invents a detail while
doing so, is worse than no tool: it produces confident wrongness about exactly
the questions where the user has no independent way to check.

So three rules are enforced in code rather than trusted to the prompt:

* The model sees numbered excerpts and nothing else. No paper text reaches it
  except through retrieval, so it cannot answer from a half-remembered version
  of a famous paper it saw in training, which for a paper like the Transformer
  it certainly did.
* Citations are validated after generation. A reference to an excerpt number
  that was never supplied is stripped, so a fabricated citation cannot survive
  to the screen looking authoritative.
* Every citation resolves to a chunk, which resolves to elements, which resolve
  to a bounding box on a page. A user can always get from a sentence in the
  answer to the place in the PDF it came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import blobs, settings
from ..db import repo
from ..llm import LLMError
from ..llm import registry as llm
from ..llm.base import ImagePart
from ..ops import meter
from ..parse.base import Section
from ..util import truncate
from . import lenses as lens_module
from .retrieve import Hit, Retrieval, search

SYSTEM = (
    "You answer questions about a research paper using only the numbered "
    "excerpts you are given.\n"
    "\n"
    "Rules, in order of importance:\n"
    "1. Use only the excerpts. If they do not contain the answer, say exactly "
    "what is missing and stop. Never fall back on what you know about this "
    "paper from anywhere else, even if you recognise it.\n"
    "2. Cite every factual claim with the excerpt number in square brackets, "
    "like [3]. A sentence containing a fact and no citation is not allowed.\n"
    "3. Copy every number, dataset name, metric and model name exactly as the "
    "excerpt gives it. Do not round, convert or tidy a number.\n"
    "4. Where excerpts disagree, present both and cite each. Do not silently "
    "pick one.\n"
    "5. Distinguish what the paper claims from what it demonstrates. 'The "
    "authors report' and 'the results show' are different statements.\n"
    "6. Where a table is marked as having inferred structure, treat its "
    "numbers as provisional and say so if you use them.\n"
    "7. Be direct. No preamble, no restating the question, no closing summary "
    "of what you just said."
)

# How much of one excerpt to show. Long enough to carry a table or a full
# paragraph, short enough that a dozen of them fit a small model's window.
EXCERPT_CHARS = 1600

_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

# Models do not reliably emit the ASCII brackets they were asked for. Groq's
# gpt-oss returns CJK lenticular brackets, and others return fullwidth forms.
# Left alone these slip past citation validation entirely, so a fabricated
# reference survives to the screen looking exactly like a real one, which is
# the failure this validation exists to prevent. They are normalised before
# anything else looks at the text.
_BRACKET_VARIANTS = {
    "【": "[", "】": "]",   # lenticular
    "［": "[", "］": "]",   # fullwidth square
    "❨": "[", "❩": "]",   # medium ornamental
    "〚": "[", "〛": "]",   # white square
}


def normalise_brackets(text: str) -> str:
    for variant, plain in _BRACKET_VARIANTS.items():
        text = text.replace(variant, plain)
    return text


@dataclass
class Answer:
    text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieval: Retrieval | None = None
    provider: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    lens: str = ""
    # Set when no model was called, so the caller can render evidence only.
    evidence_only: bool = False
    note: str = ""
    # How many figures were sent to the model as images rather than as text,
    # shown in the UI so an answer about a chart is distinguishable from one
    # about its caption.
    images_sent: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "citations": self.citations,
            "retrieval": self.retrieval.as_dict() if self.retrieval else None,
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "lens": self.lens,
            "evidence_only": self.evidence_only,
            "note": self.note,
            "images_sent": self.images_sent,
        }


def build_context(hits: Sequence[Hit]) -> tuple[str, list[dict[str, Any]]]:
    """Render hits as numbered excerpts, and the citation table that maps them.

    The numbering the model sees is one based and contiguous, because a model
    given chunk ids like 4127 will sometimes invent 4128. Small contiguous
    numbers are easy to use correctly and easy to validate.
    """
    lines: list[str] = []
    citations: list[dict[str, Any]] = []

    for index, hit in enumerate(hits, start=1):
        chunk = hit.chunk
        kind = chunk.get("kind", "text")
        section = chunk.get("section", "unknown")
        path = " > ".join(chunk.get("section_path") or [])
        page = chunk.get("page", 0)

        header = f"[{index}] {kind.upper()}"
        if hit.paper_title:
            header += f" from {truncate(hit.paper_title, 70, suffix='')}"
        if page:
            header += f", page {page}"
        if section and section != Section.UNKNOWN.value:
            header += f", {section.replace('_', ' ')}"
        if path:
            header += f" ({truncate(path, 80, suffix='')})"

        body = chunk.get("display_text") or chunk.get("text") or ""
        extra = chunk.get("extra") or {}
        if kind == "table" and extra.get("structure_confident") is False:
            body += "\n(Structure inferred, treat the columns as provisional.)"

        lines.append(f"{header}\n{truncate(body, EXCERPT_CHARS)}")
        citations.append({
            "n": index,
            "chunk_id": int(chunk["id"]),
            "paper_id": hit.paper_id,
            "paper_title": hit.paper_title,
            "kind": kind,
            "page": page,
            "section": section,
            "label": chunk.get("label", ""),
            "element_ids": chunk.get("element_ids", []),
            "image_digest": extra.get("image_digest", ""),
            "score": round(hit.score, 6),
        })

    return "\n\n".join(lines), citations


def validate_citations(
    text: str, citations: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Strip references to excerpts that were never supplied, and report what was used.

    A model that cites [14] when it was given nine excerpts has made the
    citation up, and a made up citation is more damaging than a missing one
    because it looks like provenance. The reference is removed rather than the
    sentence, so the claim survives visibly uncited and a reader can see that
    it is unsupported.
    """
    text = normalise_brackets(text or "")
    valid = {c["n"] for c in citations}
    used: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        kept = [n for n in numbers if n in valid]
        used.update(kept)
        if not kept:
            return ""
        return "[" + ", ".join(str(n) for n in kept) + "]"

    cleaned = _CITATION_RE.sub(replace, text)
    # Removing a citation can leave a double space or a space before a full
    # stop, which reads as a typo rather than as a stripped reference.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)

    return cleaned.strip(), [c for c in citations if c["n"] in used]


def ask(
    paper_ids: Sequence[int],
    question: str,
    *,
    lens: str = "",
    history: Sequence[dict[str, Any]] = (),
    provider: str | None = None,
    model: str | None = None,
    use_llm: bool = True,
    use_rerank: bool = False,
    use_dense: bool = True,
    top_k: int | None = None,
    embedding_provider: str | None = None,
    temperature: float | None = None,
) -> Answer:
    """Answer a question, or return the evidence for it when no model is wanted."""
    chosen = lens_module.get(lens) if lens else None
    question = (question or "").strip()
    if chosen and not question:
        question = chosen.description

    found = _retrieve(
        paper_ids, question, chosen,
        top_k=top_k, use_dense=use_dense, use_rerank=use_rerank,
        embedding_provider=embedding_provider,
    )
    context, citations = build_context(found.hits)

    if not found.hits:
        return Answer(
            text="",
            retrieval=found,
            lens=lens,
            evidence_only=True,
            note=(
                "Nothing in this paper matched. The paper may not be indexed yet, "
                "or the question may use wording that appears nowhere in it."
            ),
        )

    # Retrieval only mode. This is a first class result, not a failure: ranked
    # evidence with pages and scores answers "where is this discussed" exactly,
    # and it costs nothing.
    if not use_llm:
        return Answer(
            text="", citations=citations, retrieval=found, lens=lens,
            evidence_only=True,
            note="Showing retrieved evidence only. No model was called.",
        )

    if not llm.any_available():
        return Answer(
            text="", citations=citations, retrieval=found, lens=lens,
            evidence_only=True,
            note=(
                "No language model is configured, so this is the evidence "
                "without a written answer. Add a provider key in Settings, or "
                "keep using retrieval only mode."
            ),
        )

    instruction = chosen.instruction if chosen else (
        "Answer the question directly, in at most four short paragraphs."
    )
    images = vision_payload(citations)
    prompt = _prompt(question, context, instruction, history, images)

    collector = meter.start()
    try:
        raw = llm.generate(
            prompt,
            provider=provider,
            model=model,
            system=SYSTEM,
            temperature=(
                temperature if temperature is not None else settings.ANSWER_TEMPERATURE
            ),
            max_tokens=settings.ANSWER_MAX_TOKENS,
            operation=f"answer:{lens or 'question'}",
            images=images,
        )
    except LLMError as exc:
        meter.stop()
        return Answer(
            text="", citations=citations, retrieval=found, lens=lens,
            evidence_only=True,
            note=f"The model call failed: {exc.message} {exc.hint}".strip(),
        )

    entries = collector.drain()
    meter.stop()
    text, used = validate_citations(raw, citations)
    usage = {
        "input_tokens": sum(e.input_tokens for e in entries),
        "output_tokens": sum(e.output_tokens for e in entries),
        "cost_usd": round(sum(e.cost_usd for e in entries), 6),
    }
    try:
        repo.record_usage(entries, paper_id=int(paper_ids[0]) if paper_ids else None)
    except Exception:  # noqa: BLE001 - metering must never fail a request
        pass

    return Answer(
        text=text,
        citations=used or citations,
        retrieval=found,
        provider=entries[0].provider if entries else "",
        model=entries[0].model if entries else "",
        usage=usage,
        lens=lens,
        images_sent=len(images),
    )


def _retrieve(
    paper_ids: Sequence[int],
    question: str,
    chosen,
    *,
    top_k: int | None,
    use_dense: bool,
    use_rerank: bool,
    embedding_provider: str | None,
) -> Retrieval:
    """Run the question, plus a lens's own queries, and merge the results.

    A lens retrieves several times rather than once because a concept is
    phrased differently in different papers: "limitations", "threats to
    validity" and "failure cases" are the same question and share almost no
    words. Merging by fused score keeps the best evidence from each phrasing.
    """
    limit = top_k or (chosen.top_k if chosen else settings.RETRIEVE_TOP_K)
    queries = [question] if question else []
    if chosen:
        queries.extend(q for q in chosen.queries if q not in queries)

    # A comparison retrieves each paper separately and gives each the same
    # number of slots. Retrieving across the pool lets the longest or most
    # repetitive paper win every slot, and a comparison of one paper against
    # silence is worse than no comparison at all.
    if chosen is not None and lens_module.is_comparison(chosen.id) and len(paper_ids) > 1:
        return _retrieve_balanced(
            paper_ids, queries, chosen, per_paper=limit,
            use_dense=use_dense, embedding_provider=embedding_provider,
        )

    merged: dict[int, Hit] = {}
    combined = Retrieval()
    for position, query in enumerate(queries):
        found = search(
            paper_ids, query,
            top_k=limit,
            use_dense=use_dense,
            use_rerank=use_rerank and position == 0,
            embedding_provider=embedding_provider,
        )
        if position == 0:
            combined.legs_used = found.legs_used
            combined.legs_skipped = found.legs_skipped
            combined.weights = found.weights
            combined.rerank_used = found.rerank_used
        combined.candidate_count += found.candidate_count
        for hit in found.hits:
            chunk_id = int(hit.chunk["id"])
            # The question's own results outrank a lens query's, so a specific
            # question is not drowned by the lens's generic phrasings.
            weight = 1.0 if position == 0 else 0.75
            scored = hit.score * weight
            if chunk_id not in merged or merged[chunk_id].score < scored:
                hit.score = scored
                merged[chunk_id] = hit

    ordered = sorted(merged.values(), key=lambda h: h.score, reverse=True)

    if chosen and chosen.sections:
        # A preference, not a filter. Preferred sections are promoted above the
        # rest but nothing is discarded, because a paper with no limitations
        # section still has to produce an answer about its limitations.
        preferred = set(chosen.sections)
        ordered.sort(
            key=lambda h: (h.chunk.get("section") in preferred, h.score), reverse=True
        )

    combined.hits = ordered[:limit]
    return combined


def ask_streaming(
    paper_ids: Sequence[int],
    question: str,
    *,
    lens: str = "",
    history: Sequence[dict[str, Any]] = (),
    provider: str | None = None,
    model: str | None = None,
    use_rerank: bool = False,
    use_dense: bool = True,
    top_k: int | None = None,
    embedding_provider: str | None = None,
    emit=None,
) -> dict[str, Any]:
    """Answer while reporting progress, for a caller that streams to a browser.

    `emit` is called with each event as it happens and the final state is
    returned. Evidence goes out the moment retrieval finishes, because it is
    useful before a single word has been written, and on a long lens that is
    the difference between two minutes of blank screen and two minutes of
    something to read.
    """
    def say(payload: dict[str, Any]) -> None:
        if emit:
            emit(payload)

    chosen = lens_module.get(lens) if lens else None
    question = (question or "").strip()
    if chosen and not question:
        question = chosen.description

    say({"type": "status", "message": "Retrieving evidence"})
    found = _retrieve(
        paper_ids, question, chosen,
        top_k=top_k, use_dense=use_dense, use_rerank=use_rerank,
        embedding_provider=embedding_provider,
    )
    context, citations = build_context(found.hits)
    say({"type": "evidence", "citations": citations, "retrieval": found.as_dict()})

    if not found.hits:
        result = {
            "text": "", "citations": [], "retrieval": found.as_dict(),
            "provider": "", "model": "", "usage": {}, "lens": lens,
            "evidence_only": True, "images_sent": 0,
            "note": (
                "Nothing in this paper matched. The paper may not be indexed "
                "yet, or the question may use wording that appears nowhere in it."
            ),
        }
        return result

    if not llm.any_available():
        return {
            "text": "", "citations": citations, "retrieval": found.as_dict(),
            "provider": "", "model": "", "usage": {}, "lens": lens,
            "evidence_only": True, "images_sent": 0,
            "note": (
                "No language model is configured, so this is the evidence "
                "without a written answer."
            ),
        }

    instruction = chosen.instruction if chosen else (
        "Answer the question directly, in at most four short paragraphs."
    )
    images = vision_payload(citations)
    prompt = _prompt(question, context, instruction, history, images)
    if images:
        say({"type": "status", "message": f"Reading {len(images)} figure(s)"})
    say({"type": "status", "message": "Writing"})

    from ..llm import streaming

    collector = meter.start()
    pieces: list[str] = []
    try:
        for piece in streaming.stream(
            prompt,
            provider=provider, model=model, system=SYSTEM,
            temperature=settings.ANSWER_TEMPERATURE,
            max_tokens=settings.ANSWER_MAX_TOKENS,
            operation=f"answer:{lens or 'question'}",
            images=images,
        ):
            pieces.append(piece)
            say({"type": "delta", "text": piece})
    except LLMError as exc:
        meter.stop()
        return {
            "text": "".join(pieces), "citations": citations,
            "retrieval": found.as_dict(), "provider": "", "model": "",
            "usage": {}, "lens": lens, "evidence_only": not pieces,
            "images_sent": len(images),
            "note": f"The model call failed: {exc.message} {exc.hint}".strip(),
        }

    entries = collector.drain()
    meter.stop()
    text, used = validate_citations("".join(pieces), citations)
    usage = {
        "input_tokens": sum(e.input_tokens for e in entries),
        "output_tokens": sum(e.output_tokens for e in entries),
        "cost_usd": round(sum(e.cost_usd for e in entries), 6),
    }
    try:
        repo.record_usage(entries, paper_id=int(paper_ids[0]) if paper_ids else None)
    except Exception:  # noqa: BLE001 - metering must never fail a request
        pass

    return {
        # The whole text again, because citation validation rewrites it and the
        # client has only seen the raw pieces.
        "text": text,
        "citations": used or citations,
        "retrieval": found.as_dict(),
        "provider": entries[0].provider if entries else "",
        "model": entries[0].model if entries else "",
        "usage": usage,
        "lens": lens,
        "evidence_only": False,
        "images_sent": len(images),
        "note": "",
    }


def _retrieve_balanced(
    paper_ids: Sequence[int],
    queries: Sequence[str],
    chosen,
    *,
    per_paper: int,
    use_dense: bool,
    embedding_provider: str | None,
) -> Retrieval:
    """Retrieve the same number of chunks from every paper.

    Results are interleaved rather than concatenated, so the excerpt numbering
    the model sees alternates between papers. That matters: a model handed
    twenty excerpts from paper A followed by twenty from paper B tends to write
    about A and summarise B.
    """
    combined = Retrieval()
    per_paper_hits: list[list[Hit]] = []

    for index, paper_id in enumerate(paper_ids):
        merged: dict[int, Hit] = {}
        for position, query in enumerate(queries):
            found = search(
                [int(paper_id)], query,
                top_k=per_paper,
                use_dense=use_dense,
                embedding_provider=embedding_provider,
            )
            if index == 0 and position == 0:
                combined.legs_used = found.legs_used
                combined.legs_skipped = found.legs_skipped
                combined.weights = found.weights
            combined.candidate_count += found.candidate_count
            for hit in found.hits:
                chunk_id = int(hit.chunk["id"])
                scored = hit.score * (1.0 if position == 0 else 0.8)
                if chunk_id not in merged or merged[chunk_id].score < scored:
                    hit.score = scored
                    merged[chunk_id] = hit

        ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
        if chosen.sections:
            preferred = set(chosen.sections)
            ranked.sort(
                key=lambda h: (h.chunk.get("section") in preferred, h.score),
                reverse=True,
            )
        per_paper_hits.append(ranked[:per_paper])

    interleaved: list[Hit] = []
    for rank in range(max((len(h) for h in per_paper_hits), default=0)):
        for hits in per_paper_hits:
            if rank < len(hits):
                interleaved.append(hits[rank])
    combined.hits = interleaved
    return combined


def _prompt(
    question: str,
    context: str,
    instruction: str,
    history: Sequence[dict[str, Any]],
    images: Sequence[ImagePart] = (),
) -> str:
    parts: list[str] = []

    if history:
        # Only the last few turns, and truncated. Beyond that the conversation
        # crowds out the evidence, and the model starts answering from what it
        # said before rather than from what the paper says.
        recent = list(history)[-settings.MEMORY_TURNS:]
        lines = [
            f"{turn.get('role', 'user').upper()}: {truncate(turn.get('content', ''), 700)}"
            for turn in recent
            if turn.get("content")
        ]
        if lines:
            parts.append(
                "Earlier in this conversation, for reference only. Facts still "
                "have to come from the excerpts below:\n" + "\n".join(lines)
            )

    parts.append(f"EXCERPTS FROM THE PAPER:\n\n{context}")
    parts.append(f"QUESTION:\n{question}")
    parts.append(f"WHAT TO WRITE:\n{instruction}")
    return "\n\n---\n\n".join(parts)


def vision_payload(citations: Sequence[dict[str, Any]]) -> list[ImagePart]:
    """Retrieved figures and table renders as image parts.

    This is what makes a question about a chart answerable. A figure reaches
    the prompt as its caption and whatever OCR read out of it, which is often
    enough, but a model that can look at the axes reads a trend off a plot that
    no caption states.

    Capped at a handful, because full resolution figures exhaust a request
    budget quickly and the marginal one adds little.
    """
    if not settings.VISION_ENABLED:
        return []
    out: list[ImagePart] = []
    for citation in citations:
        digest = citation.get("image_digest")
        if not digest:
            continue
        data = blobs.get(digest, "image/png")
        if not data:
            continue
        out.append(ImagePart(
            data=data,
            media_type="image/png",
            label=citation.get("label") or f"excerpt {citation['n']}",
        ))
        if len(out) >= settings.MAX_VISION_IMAGES:
            break
    return out
