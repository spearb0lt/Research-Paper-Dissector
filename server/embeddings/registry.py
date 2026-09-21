"""Choosing an embedding backend, and comparing what it produces.

Selection is by declared quality among whatever is usable, so a deployment
holding a Gemini key gets Gemini, one holding no keys at all gets the bundled
ONNX model, and one where even that is missing still works through the lexical
fallback. The operator can pin a specific backend with EMBEDDING_PROVIDER.

Vectors from different backends live in different spaces and must never be
compared. `signature()` is what the pipeline stores and filters on so that a
change of provider partitions the history instead of silently corrupting it.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .. import settings
from .base import BaseEmbedder, EmbeddingError, EmbedResult
from .hosted import (
    CloudflareEmbedder,
    CohereEmbedder,
    GeminiEmbedder,
    HuggingFaceEmbedder,
    JinaEmbedder,
    OpenAIEmbedder,
    VoyageEmbedder,
)
from .local import (
    LexicalEmbedder,
    OllamaEmbedder,
    OnnxEmbedder,
    SentenceTransformersEmbedder,
)

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

_ORDER: tuple[type[BaseEmbedder], ...] = (
    OnnxEmbedder,
    GeminiEmbedder,
    CloudflareEmbedder,
    JinaEmbedder,
    VoyageEmbedder,
    OpenAIEmbedder,
    CohereEmbedder,
    SentenceTransformersEmbedder,
    OllamaEmbedder,
    HuggingFaceEmbedder,
    LexicalEmbedder,
)

_registry: dict[str, BaseEmbedder] | None = None


def embedder_map() -> dict[str, BaseEmbedder]:
    global _registry
    if _registry is None:
        built: dict[str, BaseEmbedder] = {}
        for cls in _ORDER:
            instance = cls()
            built[instance.id] = instance
        _registry = built
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None


def all_status() -> list[dict[str, Any]]:
    return [e.status().as_dict() for e in embedder_map().values()]


def available_embedders() -> list[BaseEmbedder]:
    return [e for e in embedder_map().values() if e.is_available()]


def resolve(provider_id: str | None = None) -> BaseEmbedder:
    """Pick a backend, honouring an explicit choice over the automatic one."""
    registry = embedder_map()
    wanted = (provider_id or settings.EMBEDDING_PROVIDER or "auto").strip().lower()

    if wanted and wanted != "auto":
        embedder = registry.get(wanted)
        if embedder is None:
            raise EmbeddingError(
                f"Unknown embedding provider '{wanted}'.",
                hint=f"Available: {', '.join(registry)}.",
            )
        if not embedder.is_available():
            raise EmbeddingError(
                f"{embedder.label} is not usable in this deployment.",
                provider=embedder.id,
                hint=embedder.unavailable_reason(),
            )
        return embedder

    usable = available_embedders()
    if not usable:
        # LexicalEmbedder always reports available, so reaching here means
        # numpy itself is missing and nothing can work.
        raise EmbeddingError(
            "No embedding backend is available.",
            hint="Install numpy, or set an embedding provider API key.",
        )

    # The bundled ONNX encoder is the automatic choice whenever it is usable,
    # ahead of every other backend including local ones that score higher.
    #
    # Two reasons, and the second is the important one.
    #
    # A hosted backend would quietly spend someone's quota on the highest
    # volume call in the application, and would start doing so the moment they
    # added a key for something else entirely, such as answering.
    #
    # And vectors from two backends are not comparable, so whichever backend
    # indexed a paper is the only one that can search it. The bundled model is
    # the one backend present on every deployment tier, from a serverless
    # function to a 24 GB VM. Picking it everywhere is what makes an index
    # portable: a paper parsed and indexed on a laptop stays searchable from a
    # serverless deployment that could never have parsed it.
    #
    # Ranking by quality instead looks better and is a trap. Installing the
    # server extras adds sentence-transformers, which scores higher, so the
    # automatic choice silently changes and every existing index stops being
    # searchable with the message "this paper was indexed with a different
    # backend". Nothing the user did caused that, and nothing in the UI
    # predicted it.
    #
    # EMBEDDING_PROVIDER, or the picker in Settings, names a better backend for
    # anyone who wants one and accepts re-indexing.
    for embedder in usable:
        if embedder.id == "onnx":
            return embedder
    return max(usable, key=lambda e: (e.local, e.quality))


def signature(embedder: BaseEmbedder) -> tuple[str, str]:
    """The (provider, model) pair that identifies one vector space."""
    return embedder.id, embedder.model_id()


def thresholds(embedder: BaseEmbedder) -> tuple[float, float]:
    """The (duplicate, cluster) cuts to use with this backend.

    The backend's own values are correct by default, because the right cut
    depends on the vector space. An operator who has tuned a threshold against
    their own beat sets the environment variable, and that wins.
    """
    import os

    duplicate = (
        settings.DUPLICATE_THRESHOLD
        if "DUPLICATE_THRESHOLD" in os.environ
        else embedder.duplicate_threshold
    )
    cluster = (
        settings.CLUSTER_THRESHOLD
        if "CLUSTER_THRESHOLD" in os.environ
        else embedder.cluster_threshold
    )
    return float(duplicate), float(cluster)


def embed_texts(
    texts: Sequence[str],
    *,
    provider: str | None = None,
    operation: str = "",
) -> EmbedResult:
    return resolve(provider).embed(texts, operation=operation)


# ------------------------------------------------------------- similarity


def cosine_matrix(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity between two sets of unit length rows.

    Every backend normalises before returning, so this is a plain matrix
    product. Keeping it that way matters: the deduplication stage compares each
    new article against every recent one, and at a few hundred by a few
    thousand that is one BLAS call rather than a Python loop.
    """
    import numpy as np

    if left.size == 0 or right.size == 0:
        return np.zeros((left.shape[0], right.shape[0]), dtype=np.float32)
    return np.asarray(left, dtype=np.float32) @ np.asarray(right, dtype=np.float32).T


def most_similar(
    query: np.ndarray, pool: np.ndarray, *, top_k: int = 5
) -> list[tuple[int, float]]:
    """Indices into `pool` most similar to a single query vector."""
    import numpy as np

    if pool.size == 0 or query.size == 0:
        return []
    scores = np.asarray(pool, dtype=np.float32) @ np.asarray(query, dtype=np.float32)
    count = min(top_k, scores.shape[0])
    # argpartition finds the top k without sorting the whole array, which
    # matters once the archive is large enough for search to be useful.
    idx = np.argpartition(-scores, count - 1)[:count]
    idx = idx[np.argsort(-scores[idx])]
    return [(int(i), float(scores[i])) for i in idx]
