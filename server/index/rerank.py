"""Cross encoder reranking, off by default and honest about why.

A cross encoder reads the query and one candidate together and scores their
relevance directly, rather than comparing two vectors that were computed
without knowledge of each other. Every benchmark that measures it finds it the
single largest quality improvement available in a retrieval pipeline.

It is off by default here regardless, for two reasons that are specific to this
application rather than to reranking in general:

* It needs PyTorch, which is about a gigabyte and does not exist on the
  serverless tier at all. Making the default depend on it would mean the
  default configuration fails on one of the deployment targets.
* It scores every candidate, so latency is linear in the candidate count. On
  forty candidates on a CPU that is a second or two, which is fine for a
  question and wrong for the as-you-type search in the elements view.

So it is a per-request switch, reported as unavailable with a reason where it
cannot run, and the UI says what turning it on costs.
"""
from __future__ import annotations

import threading
from typing import Any, Sequence

from .. import settings
from ..runtime import current as runtime

_model = None
_model_name = ""
_lock = threading.Lock()


def available() -> bool:
    return runtime().can("rerank")


def unavailable_reason() -> str:
    return runtime().reason("rerank")


def _load(name: str):
    """Load the cross encoder once per process.

    Under a lock because two concurrent requests both finding `_model` None
    would otherwise each load a copy, and two copies of a transformer is how a
    512 MB container dies.
    """
    global _model, _model_name
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(name, max_length=512)
        _model_name = name
        return _model


def rerank(
    query: str,
    candidates: Sequence[dict[str, Any]],
    *,
    text_of,
    top_k: int | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Reorder candidates by cross encoder relevance.

    Returns the input unchanged when reranking is unavailable, rather than
    raising: a request that asked for it on a machine that cannot do it should
    still get results, with `rerank_used` false so the caller can say so.
    """
    if not candidates or not available():
        return list(candidates)

    name = model or settings.RERANK_MODEL
    try:
        encoder = _load(name)
        pairs = [(query, text_of(c) or "") for c in candidates]
        scores = encoder.predict(pairs, show_progress_bar=False)
    except Exception:  # noqa: BLE001 - a failed rerank must not lose the results
        return list(candidates)

    ordered = sorted(
        zip(candidates, scores), key=lambda pair: float(pair[1]), reverse=True
    )
    out: list[dict[str, Any]] = []
    for entry, score in ordered:
        copied = dict(entry)
        copied["rerank_score"] = round(float(score), 6)
        out.append(copied)
    return out[: (top_k or settings.RERANK_TOP_K)]
