"""Finding the evidence, with or without a model involved.

This module is the whole of the no-LLM mode. A search returns ranked chunks
with their scores, their pages, the elements behind them, and a per leg
breakdown of why each one ranked where it did. That is a complete and useful
product on its own: it answers "where in this paper is X" exactly, it costs
nothing, it needs no key, and it is what the answering layer is built on rather
than a degraded version of it.

The retrieval itself is hybrid and fused. Three legs run independently:

    lexical   BM25 over chunk text, which finds exact values and coded terms
    dense     cosine over local embeddings, which finds paraphrase
    visual    CLIP over figures, when enabled

and reciprocal rank fusion combines them without needing their scores to be
comparable. A cross encoder reranks the survivors when it is available and the
caller asked for it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import settings
from ..db import repo
from ..embeddings.base import EmbeddingError
from ..index import fuse, lexical, rerank
from ..index import dense as dense_module
from ..index.lexical import highlight


# "Figure 3", "Fig. 3a", "Table 2", "Eq. 7". A query naming one of these is
# making an exact reference, not describing a topic.
_LABEL_REFERENCE = re.compile(
    r"\b(fig(?:ure)?|tab(?:le)?|eq(?:uation)?|alg(?:orithm)?|scheme|listing)\.?\s*"
    r"([0-9]+(?:\.[0-9]+)*[a-z]?)\b",
    re.IGNORECASE,
)

_LABEL_CANONICAL = {
    "fig": "figure", "figure": "figure",
    "tab": "table", "table": "table",
    "eq": "equation", "equation": "equation",
    "alg": "algorithm", "algorithm": "algorithm",
    "scheme": "scheme", "listing": "listing",
}


def label_references(query: str) -> list[str]:
    """Labels a query names explicitly, normalised to "figure 3" form."""
    out: list[str] = []
    for word, number in _LABEL_REFERENCE.findall(query or ""):
        kind = _LABEL_CANONICAL.get(word.lower().rstrip("."), word.lower())
        reference = f"{kind} {number}"
        if reference not in out:
            out.append(reference)
    return out


@dataclass
class Hit:
    chunk: dict[str, Any]
    score: float
    legs: dict[str, Any] = field(default_factory=dict)
    rerank_score: float | None = None
    paper_id: int = 0
    paper_title: str = ""

    def as_dict(self) -> dict[str, Any]:
        chunk = self.chunk
        return {
            "chunk_id": int(chunk["id"]),
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "kind": chunk.get("kind", "text"),
            "text": chunk.get("display_text") or chunk.get("text", ""),
            "page": chunk.get("page", 0),
            "section": chunk.get("section", "unknown"),
            "section_path": chunk.get("section_path", []),
            "label": chunk.get("label", ""),
            "caption": chunk.get("caption", ""),
            "element_ids": chunk.get("element_ids", []),
            "score": round(self.score, 6),
            "legs": self.legs,
            "rerank_score": self.rerank_score,
            "image_digest": (chunk.get("extra") or {}).get("image_digest", ""),
            "extra": chunk.get("extra") or {},
        }


@dataclass
class Retrieval:
    hits: list[Hit] = field(default_factory=list)
    legs_used: list[str] = field(default_factory=list)
    legs_skipped: dict[str, str] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    rerank_used: bool = False
    candidate_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": [h.as_dict() for h in self.hits],
            "legs_used": self.legs_used,
            "legs_skipped": self.legs_skipped,
            "weights": self.weights,
            "rerank_used": self.rerank_used,
            "candidate_count": self.candidate_count,
        }


def search(
    paper_ids: Sequence[int],
    query: str,
    *,
    top_k: int | None = None,
    candidate_k: int | None = None,
    use_dense: bool = True,
    use_lexical: bool = True,
    use_rerank: bool = False,
    kinds: Sequence[str] = (),
    sections: Sequence[str] = (),
    embedding_provider: str | None = None,
    diversify: bool = True,
) -> Retrieval:
    """Hybrid retrieval over one or more papers."""
    query = (query or "").strip()
    out = Retrieval()
    if not query or not paper_ids:
        return out

    top_k = top_k or settings.RETRIEVE_TOP_K
    candidate_k = candidate_k or settings.CANDIDATE_K
    out.weights = fuse.weights_for_query(query)

    lexical_ranked: list[tuple[Any, float]] = []
    dense_ranked: list[tuple[Any, float]] = []
    query_vector: list[float] | None = None

    for paper_id in paper_ids:
        lexical_blob, dense_blob, signature = repo.load_indexes(int(paper_id))

        if use_lexical and lexical_blob:
            index = lexical.BM25Index.loads(lexical_blob)
            if index is None:
                out.legs_skipped["lexical"] = (
                    "The keyword index was built by an older version and needs rebuilding."
                )
            else:
                lexical_ranked.extend(index.search(query, top_k=candidate_k))
        elif use_lexical:
            out.legs_skipped.setdefault("lexical", "This paper has no keyword index yet.")

        if use_dense and dense_blob:
            index = dense_module.DenseIndex.loads(dense_blob)
            if index is None:
                out.legs_skipped["dense"] = (
                    "The vector index could not be read and needs rebuilding."
                )
            else:
                if query_vector is None:
                    try:
                        query_vector = dense_module.embed_query(
                            query, provider_id=embedding_provider
                        )
                    except EmbeddingError as exc:
                        out.legs_skipped["dense"] = str(exc)
                        query_vector = []
                # A query embedded by one backend cannot be compared against a
                # matrix built by another. Silently comparing them returns
                # plausible looking neighbours that are pure noise, which is
                # far worse than skipping the leg and saying so.
                if query_vector and signature and not _compatible(
                    signature, index, embedding_provider
                ):
                    out.legs_skipped["dense"] = (
                        f"This paper was indexed with {signature.split(':')[0]}, which is "
                        "not the embedding backend in use now. Re-index it to search it."
                    )
                elif query_vector:
                    dense_ranked.extend(index.search(query_vector, top_k=candidate_k))
        elif use_dense:
            out.legs_skipped.setdefault(
                "dense",
                "This paper has no vector index. Re-index it with embeddings enabled.",
            )

    legs: dict[str, Sequence[tuple[Any, float]]] = {}
    if lexical_ranked:
        # Each paper contributed its own ranking, so a multi paper search has
        # to re-sort before fusion or paper order would leak into rank order.
        legs["lexical"] = sorted(lexical_ranked, key=lambda kv: kv[1], reverse=True)
        out.legs_used.append("lexical")
    if dense_ranked:
        legs["dense"] = sorted(dense_ranked, key=lambda kv: kv[1], reverse=True)
        out.legs_used.append("dense")

    if not legs:
        return out

    fused = fuse.reciprocal_rank_fusion(
        legs,
        weights=out.weights,
        top_k=max(top_k * 3, candidate_k) if (use_rerank or diversify) else top_k,
    )
    out.candidate_count = len(fused)

    chunk_ids = [int(entry["id"]) for entry in fused]
    chunks = {int(c["id"]): c for c in repo.get_chunks_by_ids(chunk_ids)}
    titles = _titles(paper_ids)

    hits: list[Hit] = []
    for entry in fused:
        chunk = chunks.get(int(entry["id"]))
        if chunk is None:
            continue
        if kinds and chunk.get("kind") not in kinds:
            continue
        if sections and chunk.get("section") not in sections:
            continue
        paper_id = int(chunk["paper_id"])
        hits.append(Hit(
            chunk=chunk,
            score=float(entry["score"]),
            legs=entry["legs"],
            paper_id=paper_id,
            paper_title=titles.get(paper_id, ""),
        ))

    if use_rerank and rerank.available():
        reranked = rerank.rerank(
            query,
            [{"hit": h} for h in hits],
            text_of=lambda entry: entry["hit"].chunk.get("display_text")
            or entry["hit"].chunk.get("text", ""),
            top_k=max(top_k, settings.RERANK_TOP_K),
        )
        rebuilt: list[Hit] = []
        for entry in reranked:
            hit = entry["hit"]
            hit.rerank_score = entry.get("rerank_score")
            rebuilt.append(hit)
        hits = rebuilt
        out.rerank_used = True
    elif use_rerank:
        out.legs_skipped["rerank"] = rerank.unavailable_reason()

    if diversify:
        wrapped = fuse.diversify(
            [{"hit": h} for h in hits],
            key=lambda entry: (entry["hit"].paper_id, entry["hit"].chunk.get("section")),
            max_per_group=4,
        )
        hits = [entry["hit"] for entry in wrapped]

    hits = _pin_referenced_labels(paper_ids, query, hits)
    out.hits = hits[:top_k]
    return out


def _pin_referenced_labels(
    paper_ids: Sequence[int], query: str, hits: list[Hit]
) -> list[Hit]:
    """Hoist the element a query names by label to the front.

    "What does Figure 3 show" is an exact reference, and ranking it like a
    topic loses it: "figure" matches every figure in the paper and "3" carries
    almost no signal, so asking about Figure 3 returned Figures 4 and 5 and a
    correct model then reported that Figure 3 was not among the excerpts.

    Looked up directly against the stored labels and put first, so a question
    about a specific figure or table reaches that figure or table.
    """
    references = label_references(query)
    if not references:
        return hits

    wanted = {r.lower() for r in references}
    pinned: list[Hit] = []
    seen = {int(hit.chunk["id"]) for hit in hits}
    titles = _titles(paper_ids)

    for paper_id in paper_ids:
        for chunk in repo.list_chunks(int(paper_id)):
            label = (chunk.get("label") or "").strip().lower()
            if not label or label not in wanted:
                continue
            chunk_id = int(chunk["id"])
            existing = next((h for h in hits if int(h.chunk["id"]) == chunk_id), None)
            if existing is not None:
                # The score is raised, not just the position. A caller that
                # merges several retrievals re-sorts by score, and a pin that
                # lives only in list order does not survive that: it was
                # putting the named figure back at rank ten.
                existing.score = 1.0
                existing.legs = {**existing.legs, "label": {"rank": 1, "score": 1.0}}
                pinned.append(existing)
                hits = [h for h in hits if int(h.chunk["id"]) != chunk_id]
                continue
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            pinned.append(Hit(
                chunk=chunk,
                # Above anything fusion produced, because this is an exact
                # reference rather than a ranking.
                score=1.0,
                legs={"label": {"rank": 1, "score": 1.0}},
                paper_id=int(paper_id),
                paper_title=titles.get(int(paper_id), ""),
            ))

    return pinned + hits if pinned else hits


def _compatible(signature: str, index, embedding_provider: str | None) -> bool:
    """Whether the current embedder produces vectors in this index's space."""
    from ..embeddings import registry as embeddings

    try:
        embedder = embeddings.resolve(embedding_provider)
    except EmbeddingError:
        return False
    return signature == f"{embedder.id}:{embedder.model_id()}:{index.dim}"


def _titles(paper_ids: Sequence[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for paper_id in paper_ids:
        paper = repo.get_paper(int(paper_id))
        if paper:
            out[int(paper_id)] = paper.get("title", "") or ""
    return out


def find_everywhere(
    paper_ids: Sequence[int], needle: str, *, limit: int = 200
) -> dict[str, Any]:
    """Every place a term appears, across every element kind.

    This is what backs the All Views search box, and it is deliberately not
    retrieval. Retrieval ranks and returns the best few; this is exhaustive and
    literal, because a reader asking where a term appears wants all of them,
    including the one inside a table cell on page 14 and the one OCR read out
    of a figure legend.
    """
    needle = (needle or "").strip()
    if not needle or not paper_ids:
        return {"total": 0, "by_kind": {}, "matches": []}

    rows = repo.search_elements([int(p) for p in paper_ids], needle, limit=limit)
    titles = _titles(paper_ids)

    matches: list[dict[str, Any]] = []
    by_kind: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("kind", "other"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        snippets = highlight(row.get("search_text") or "", needle)
        matches.append({
            "element_id": int(row["id"]),
            "paper_id": int(row["paper_id"]),
            "paper_title": titles.get(int(row["paper_id"]), ""),
            "kind": kind,
            "page": row.get("page", 0),
            "section": row.get("section", "unknown"),
            "label": row.get("label", ""),
            "caption": row.get("caption", ""),
            "bbox": row.get("bbox"),
            "image_digest": (row.get("image") or {}).get("digest", ""),
            "snippets": snippets,
            # Where in this element the term was found, so the UI can say
            # "in a table cell" rather than just "in a table".
            "where": _where(row, needle),
        })

    return {"total": len(matches), "by_kind": by_kind, "matches": matches}


def _where(row: dict[str, Any], needle: str) -> list[str]:
    """Which parts of an element carry the term."""
    lowered = needle.lower()
    found: list[str] = []
    if lowered in (row.get("text") or "").lower():
        found.append("body")
    if lowered in (row.get("caption") or "").lower():
        found.append("caption")
    if lowered in (row.get("label") or "").lower():
        found.append("label")
    image = row.get("image") or {}
    if lowered in (image.get("ocr_text") or "").lower():
        found.append("text inside the figure")
    if lowered in (image.get("description") or "").lower():
        found.append("figure description")
    table = row.get("table") or {}
    for grid_row in table.get("grid") or []:
        if any(lowered in (cell or "").lower() for cell in grid_row):
            found.append("table cell")
            break
    return found or ["body"]
