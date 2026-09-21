"""Combining retrieval legs with reciprocal rank fusion.

Three legs produce ranked lists that are not comparable to each other. BM25
returns an unbounded score whose scale depends on the corpus, cosine returns
something between minus one and one, and CLIP returns a cosine in a different
space entirely. Normalising them onto a shared scale requires knowing each
one's distribution, which changes per paper and per query.

Reciprocal rank fusion sidesteps that by throwing the scores away and using
only the ranks: a document's fused score is the sum over legs of 1/(k + rank).
It needs no tuning, no calibration and no knowledge of any leg's scale, and it
is what every benchmark that reports numbers on hybrid retrieval actually uses.
k is 60, the value from the original paper.

The one thing RRF does not do is weight a leg by how much it should be trusted
for a given query. "What was the BLEU score" is a lexical question and "what
problem does this solve" is a semantic one, and a fixed fusion treats them
alike. `weights` exists for that, and `weights_for_query` sets them from cheap
signals in the query itself rather than from a model.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from .. import settings

# A query carrying any of these is asking for a specific value, where the
# lexical leg is the one that can actually find it.
_NUMERIC_QUERY = re.compile(
    r"\d|\bhow (many|much|long|often)\b|\bwhat (score|value|number|rate|size|"
    r"accuracy|f1|bleu|auc|p-value|percentage)\b|\bequation\b|\bhyper-?parameter",
    re.IGNORECASE,
)
# and any of these is asking about meaning, where the dense leg leads.
_CONCEPTUAL_QUERY = re.compile(
    r"\bwhy\b|\bhow does\b|\bexplain\b|\bcompare\b|\bimplication|\bintuition\b|"
    r"\bmotivat|\brationale\b|\bsummar|\bcontribut|\bnovel|\blimitation",
    re.IGNORECASE,
)
# and any of these wants something you look at.
_VISUAL_QUERY = re.compile(
    r"\bfigure\b|\bfig\.?\s*\d|\bchart\b|\bgraph\b|\bplot\b|\bdiagram\b|"
    r"\barchitecture\b|\bimage\b|\bvisuali|\bshown? in\b|\bdepict",
    re.IGNORECASE,
)


def weights_for_query(query: str) -> dict[str, float]:
    """Set per leg weights from the shape of the question.

    Deliberately mild. The range is 0.6 to 1.5, so a misread never silences a
    leg, it only reorders within what all three already found. A classifier
    here would be more accurate and would also be a model call on the latency
    path of every search, for a gain that fusion largely provides anyway.
    """
    weights = {"lexical": 1.0, "dense": 1.0, "visual": 0.7}
    text = query or ""
    if _NUMERIC_QUERY.search(text):
        weights["lexical"] = 1.5
        weights["dense"] = 0.85
    if _CONCEPTUAL_QUERY.search(text):
        weights["dense"] = 1.4
        weights["lexical"] = max(0.6, weights["lexical"] * 0.75)
    if _VISUAL_QUERY.search(text):
        weights["visual"] = 1.5
    return weights


def reciprocal_rank_fusion(
    legs: dict[str, Sequence[tuple[Any, float]]],
    *,
    weights: dict[str, float] | None = None,
    k: int | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Fuse ranked lists into one.

    Returns dicts carrying the fused score and each leg's own rank and score,
    because a result the user cannot explain is a result they cannot trust, and
    the retrieval-only mode shows exactly this breakdown.
    """
    k = k or settings.RRF_K
    weights = weights or {}
    top_k = top_k or settings.RETRIEVE_TOP_K

    fused: dict[Any, dict[str, Any]] = {}
    for leg_name, ranked in legs.items():
        weight = float(weights.get(leg_name, 1.0))
        if weight <= 0:
            continue
        for position, (doc_id, score) in enumerate(ranked):
            entry = fused.setdefault(doc_id, {"id": doc_id, "score": 0.0, "legs": {}})
            entry["score"] += weight / (k + position + 1)
            entry["legs"][leg_name] = {"rank": position + 1, "score": round(float(score), 6)}

    out = sorted(fused.values(), key=lambda e: e["score"], reverse=True)
    for entry in out:
        entry["score"] = round(entry["score"], 6)
    return out[:top_k]


def diversify(
    results: list[dict[str, Any]],
    *,
    key,
    max_per_group: int = 3,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Stop one section or one page taking every slot.

    A question about results retrieves five adjacent chunks of the results
    section, all saying nearly the same thing, and the answer is then built
    from one paragraph seen five times. Capping per group and back-filling from
    what is left keeps the evidence varied without dropping the best hit.
    """
    kept: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    seen: dict[Any, int] = {}
    for entry in results:
        group = key(entry)
        count = seen.get(group, 0)
        if count < max_per_group:
            seen[group] = count + 1
            kept.append(entry)
        else:
            overflow.append(entry)
    kept.extend(overflow)
    return kept[:limit] if limit else kept


def merge_ids(legs: Iterable[Sequence[tuple[Any, float]]]) -> list[Any]:
    """Every id any leg returned, in first-seen order."""
    seen: set[Any] = set()
    out: list[Any] = []
    for ranked in legs:
        for doc_id, _ in ranked:
            if doc_id not in seen:
                seen.add(doc_id)
                out.append(doc_id)
    return out
