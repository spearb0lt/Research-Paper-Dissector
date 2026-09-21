"""Dense retrieval by brute force cosine, with no vector database.

A paper is a few hundred to a few thousand chunks. At 384 dimensions that is a
matrix of at most a few megabytes, and scoring a query against all of it is one
`numpy` matrix product: well under a millisecond, with no index to build, no
approximation, and exact results.

The previous version of this project used Chroma, and its own documentation
records the cost: Chroma's HNSW backend serialises segment files per client, so
two collections sharing a directory leave one of them with metadata in SQLite
and no segment files on disk, failing at load with "Nothing found on disk". The
workaround was a second directory and a second client for the visual index.
None of that is needed at this scale, and removing it is also what makes a
serverless deployment possible, since Chroma does not fit in the bundle.

Vectors from different backends are not comparable. `signature()` records which
backend and model produced a matrix, and a search against a matrix built by a
different one rebuilds rather than returning quietly wrong neighbours.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Sequence

from .. import settings
from ..embeddings import registry as embeddings
from ..embeddings.base import EmbeddingError

FORMAT = "dense-v1"


@dataclass
class DenseIndex:
    """A float32 matrix of L2 normalised row vectors, plus what made it."""

    matrix: Any = None            # numpy.ndarray, shape (n, dim)
    doc_ids: list[Any] = None     # type: ignore[assignment]
    provider: str = ""
    model: str = ""
    dim: int = 0

    def __post_init__(self) -> None:
        if self.doc_ids is None:
            self.doc_ids = []

    @property
    def size(self) -> int:
        return len(self.doc_ids)

    def signature(self) -> str:
        return f"{self.provider}:{self.model}:{self.dim}"

    def search(self, query_vector: Sequence[float], top_k: int = 40) -> list[tuple[Any, float]]:
        """Cosine similarity against every row, exactly.

        Rows are already normalised, so the dot product is the cosine and no
        per-query normalisation of the corpus is needed.
        """
        import numpy as np

        if self.matrix is None or not self.size:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            return []
        query = query / norm

        scores = self.matrix @ query
        count = min(top_k, scores.shape[0])
        # argpartition finds the top k without sorting the rest, which is the
        # difference between O(n) and O(n log n) once a corpus is large.
        top = np.argpartition(-scores, count - 1)[:count]
        top = top[np.argsort(-scores[top])]
        return [(self.doc_ids[int(i)], round(float(scores[int(i)]), 6)) for i in top]

    def dumps(self) -> bytes:
        """Serialise the matrix and its provenance into one blob.

        The id type is recorded and restored. Writing ids as strings and
        reading them back as strings looks harmless and is not: the lexical
        index returns integer chunk ids, so rank fusion saw "37" and 37 as two
        different documents and never merged the legs. Hybrid search then ran
        as two independent single leg searches, which is invisible in the
        output and halves the quality.
        """
        import numpy as np

        integral = all(isinstance(d, int) or str(d).lstrip("-").isdigit() for d in self.doc_ids)
        buffer = io.BytesIO()
        np.savez_compressed(
            buffer,
            matrix=self.matrix if self.matrix is not None else np.zeros((0, 0), dtype=np.float32),
            doc_ids=np.array([str(d) for d in self.doc_ids], dtype=object),
            meta=np.array(
                [FORMAT, self.provider, self.model, str(self.dim),
                 "int" if integral else "str"],
                dtype=object,
            ),
        )
        return buffer.getvalue()

    @classmethod
    def loads(cls, blob: bytes) -> "DenseIndex | None":
        import numpy as np

        try:
            data = np.load(io.BytesIO(blob), allow_pickle=True)
            meta = list(data["meta"])
            if not meta or meta[0] != FORMAT:
                return None
            integral = len(meta) > 4 and str(meta[4]) == "int"
            raw_ids = [str(d) for d in data["doc_ids"]]
            return cls(
                matrix=data["matrix"],
                doc_ids=[int(d) for d in raw_ids] if integral else raw_ids,
                provider=str(meta[1]),
                model=str(meta[2]),
                dim=int(meta[3]),
            )
        except Exception:  # noqa: BLE001 - a corrupt index is rebuilt, not fatal
            return None


def build(
    documents: Sequence[tuple[Any, str]],
    *,
    provider_id: str | None = None,
    progress=None,
) -> DenseIndex:
    """Embed every document and stack the vectors into one normalised matrix.

    Raises `EmbeddingError` rather than returning an empty index, because a
    silent empty dense leg looks exactly like a working one whose recall is
    mysteriously bad.
    """
    import numpy as np

    embedder = embeddings.resolve(provider_id)
    if not documents:
        return DenseIndex(
            matrix=np.zeros((0, 0), dtype=np.float32),
            doc_ids=[],
            provider=embedder.id,
            model=embedder.model_id(),
            dim=0,
        )

    doc_ids = [doc_id for doc_id, _ in documents]
    texts = [text or "" for _, text in documents]

    vectors: list[list[float]] = []
    batch_size = max(1, min(settings.EMBEDDING_BATCH_SIZE, getattr(embedder, "max_batch", 32)))
    for start in range(0, len(texts), batch_size):
        window = texts[start:start + batch_size]
        result = embedder.embed(window, operation="index")
        vectors.extend(result.vectors)
        if progress:
            progress(min(start + batch_size, len(texts)), len(texts))

    if len(vectors) != len(doc_ids):
        raise EmbeddingError(
            f"The embedder returned {len(vectors)} vectors for {len(doc_ids)} chunks."
        )

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector comes back for an empty or unencodable chunk. Dividing by
    # zero would poison the whole matrix with NaN, which then scores every
    # query as NaN and returns nothing, with no error anywhere.
    norms[norms == 0.0] = 1.0
    matrix = matrix / norms

    return DenseIndex(
        matrix=matrix,
        doc_ids=doc_ids,
        provider=embedder.id,
        model=embedder.model_id(),
        dim=int(matrix.shape[1]) if matrix.size else 0,
    )


def embed_query(text: str, *, provider_id: str | None = None) -> list[float]:
    """Embed one query. Returned as a plain list, not a numpy row.

    A numpy array has no useful truth value, so a caller writing the obvious
    `if query_vector:` gets a ValueError rather than a boolean. Converting at
    this boundary means no caller has to know.
    """
    embedder = embeddings.resolve(provider_id)
    return [float(v) for v in embedder.embed([text], operation="search").vectors[0]]
