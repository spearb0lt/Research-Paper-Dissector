"""BM25 over a paper's chunks and elements.

Written here rather than taken from a library for one reason: tokenisation. A
paper's most discriminating terms are "41.0", "BLEU-4", "p<0.05", "F1",
"10^-4", "BERT-base". Every off the shelf tokeniser splits those into pieces,
and a retrieval system that has split "41.0" into "41" and "0" cannot answer
"what BLEU score did they report", which is close to the most common question
anyone asks a results table. `science_tokens()` in util.py keeps them whole,
and this index is built on it.

The lexical leg matters more here than in a general RAG system. Dense retrieval
is good at "what problem does this paper solve" and reliably poor at "what was
the learning rate", because an embedding of a number carries almost no signal.
The old version of this project worked around that by keeping a second dense
index over raw text alongside the one over summaries. BM25 does the job
properly, costs nothing to run, and needs no model.

Scale note: a paper is a few hundred to a few thousand chunks. The posting
lists are built in memory and scored with a dictionary walk, which is
microseconds at this size. Nothing here would survive a million documents and
nothing here needs to.
"""
from __future__ import annotations

import math
import pickle
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..util import science_tokens

# Okapi BM25's two parameters. k1 controls how fast term frequency saturates
# and b how strongly length is normalised. These are the standard values and
# they are standard because they are hard to beat without tuning per corpus.
K1 = 1.2
B = 0.75

# Format tag written into every serialised index. A change to the tokeniser
# changes what the postings mean, so an index built by an older version has to
# be rebuilt rather than loaded and quietly mis-scored.
FORMAT = "bm25-v1"


@dataclass
class BM25Index:
    postings: dict[str, dict[int, int]] = field(default_factory=dict)
    lengths: list[int] = field(default_factory=list)
    doc_ids: list[Any] = field(default_factory=list)
    average_length: float = 0.0
    format: str = FORMAT

    @property
    def size(self) -> int:
        return len(self.doc_ids)

    def _idf(self, term: str) -> float:
        """Robertson and Sparck Jones inverse document frequency.

        The +0.5 terms make this negative for a term present in more than half
        the documents, which would let a stopword-ish term subtract from a
        score. Clamping at a small positive floor is the usual fix and is what
        every production implementation does.
        """
        frequency = len(self.postings.get(term, ()))
        if not frequency:
            return 0.0
        value = math.log(1.0 + (self.size - frequency + 0.5) / (frequency + 0.5))
        return max(value, 1e-6)

    def search(self, query: str, top_k: int = 40) -> list[tuple[Any, float]]:
        """Rank documents for a query. Returns (doc_id, score), best first."""
        terms = science_tokens(query)
        if not terms or not self.size:
            return []

        scores: dict[int, float] = {}
        for term, query_frequency in Counter(terms).items():
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = self._idf(term)
            # A term repeated in the query counts more, damped the same way
            # document frequency is, so "bleu bleu bleu" is not three times
            # "bleu".
            query_weight = (query_frequency * (K1 + 1)) / (query_frequency + K1)
            for index, frequency in posting.items():
                length_ratio = self.lengths[index] / self.average_length if self.average_length else 1.0
                denominator = frequency + K1 * (1 - B + B * length_ratio)
                scores[index] = scores.get(index, 0.0) + idf * query_weight * (
                    frequency * (K1 + 1) / denominator
                )

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self.doc_ids[index], round(score, 6)) for index, score in ranked]

    def contains(self, term: str) -> bool:
        return term in self.postings

    def dumps(self) -> bytes:
        """Serialise for storage next to the paper.

        Pickle is safe here because nothing outside this process ever writes
        one: indexes are built from parsed papers and stored in the app's own
        database. An index loaded from anywhere else would be a much larger
        problem than its format.
        """
        return pickle.dumps(
            {
                "format": self.format,
                "postings": self.postings,
                "lengths": self.lengths,
                "doc_ids": self.doc_ids,
                "average_length": self.average_length,
            },
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    @classmethod
    def loads(cls, blob: bytes) -> "BM25Index | None":
        try:
            data = pickle.loads(blob)
        except Exception:  # noqa: BLE001 - a corrupt index is rebuilt, not fatal
            return None
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None
        return cls(
            postings=data["postings"],
            lengths=data["lengths"],
            doc_ids=data["doc_ids"],
            average_length=data["average_length"],
        )


def build(documents: Iterable[tuple[Any, str]]) -> BM25Index:
    """Build an index from (doc_id, text) pairs."""
    index = BM25Index()
    total = 0
    for doc_id, text in documents:
        tokens = science_tokens(text or "")
        position = len(index.doc_ids)
        index.doc_ids.append(doc_id)
        index.lengths.append(len(tokens))
        total += len(tokens)
        for term, frequency in Counter(tokens).items():
            index.postings.setdefault(term, {})[position] = frequency
    index.average_length = (total / len(index.doc_ids)) if index.doc_ids else 0.0
    return index


def highlight(text: str, query: str, *, window: int = 220) -> list[dict[str, Any]]:
    """Find where a query's terms appear, for keyword in context display.

    Returns snippets with the matched span marked by character offsets rather
    than by inserting markup, so the caller decides how to render it and no
    HTML is ever built here. Building it here is how a document viewer becomes
    an injection vector.
    """
    if not text or not query:
        return []
    terms = {t for t in science_tokens(query) if len(t) > 1}
    if not terms:
        return []

    lowered = text.lower()
    hits: list[tuple[int, int]] = []
    for term in terms:
        start = 0
        while True:
            found = lowered.find(term, start)
            if found == -1:
                break
            # Whole token only, so "art" does not light up inside "state".
            before = lowered[found - 1] if found else " "
            after = lowered[found + len(term)] if found + len(term) < len(lowered) else " "
            if not (before.isalnum() or after.isalnum()):
                hits.append((found, found + len(term)))
            start = found + len(term)

    if not hits:
        return []
    hits.sort()

    out: list[dict[str, Any]] = []
    for start, end in hits:
        if out and start < out[-1]["end"] + window // 2:
            # Extend the previous snippet rather than opening an overlapping one.
            out[-1]["end"] = max(out[-1]["end"], end)
            out[-1]["spans"].append([start, end])
            continue
        out.append({"start": start, "end": end, "spans": [[start, end]]})

    snippets: list[dict[str, Any]] = []
    for entry in out[:20]:
        left = max(0, entry["start"] - window // 2)
        right = min(len(text), entry["end"] + window // 2)
        # Do not cut a word in half at either edge.
        if left > 0:
            space = text.find(" ", left)
            left = space + 1 if 0 <= space < left + 30 else left
        if right < len(text):
            space = text.rfind(" ", right - 30, right)
            right = space if space > 0 else right
        snippets.append({
            "text": text[left:right],
            "prefix": "..." if left > 0 else "",
            "suffix": "..." if right < len(text) else "",
            "spans": [[s - left, e - left] for s, e in entry["spans"] if s >= left and e <= right],
        })
    return snippets
