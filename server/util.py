"""Small shared helpers: text normalisation, tokenisation, hashing, JSON.

Two tokenisers live here and they are not interchangeable.

`tokens()` is the news style tokeniser the lexical embedding backend was
calibrated against. Its thresholds in embeddings/local.py were measured with
this exact function, so changing it silently changes retrieval quality. It is
kept byte for byte.

`science_tokens()` is what BM25 uses over paper text. A paper's most
discriminating terms are exactly the ones a word tokeniser destroys: "41.0",
"BLEU-4", "p<0.05", "F1", "CO2", "10^-4", "BERT-base". Splitting those into
"41" and "0" is how a retrieval system ends up unable to answer "what was the
BLEU score", which is the single most common kind of question asked of a
results table.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

# -------------------------------------------------------------------- time


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    """ISO 8601 UTC with a trailing Z, the only timestamp format stored."""
    return utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


# -------------------------------------------------------------------- text

_WORD_RE = re.compile(r"[a-z0-9]+")
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")

_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "of", "in", "on", "at", "to", "for", "with", "from", "by", "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "he", "she", "they", "them", "we", "you", "i", "not", "no", "nor", "so", "such", "up", "out", "over", "under", "again", "further", "once", "here", "there", "all", "any", "both", "each", "few", "more", "most", "other", "some", "only", "own", "same", "too", "very", "can", "will", "just", "do", "does", "did", "doing", "have", "has", "had", "having", "would", "could", "should", "may", "might", "said", "says", "say", "new", "news", "report", "reports", "update", "updates", "after", "before", "amid"]
)

# Stopwords for academic prose. Much smaller than the news list, because a word
# like "results" or "method" is genuinely discriminating inside a paper even
# though it is frequent. Only words that appear in essentially every sentence
# of every paper are dropped.
_PAPER_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
     "from", "by", "as", "is", "are", "was", "were", "be", "been", "being",
     "it", "its", "that", "this", "these", "those", "we", "our", "they", "their",
     "which", "such", "can", "may", "also", "however", "thus", "therefore"]
)


def strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = _TAG_RE.sub(" ", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&rsquo;", "'"),
        ("&ldquo;", '"'), ("&rdquo;", '"'),
    ):
        cleaned = cleaned.replace(entity, char)
    return _WS_RE.sub(" ", cleaned).strip()


def normalise_text(text: str) -> str:
    """Lowercased, accent folded, whitespace collapsed."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _WS_RE.sub(" ", folded.lower()).strip()


def tokens(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Word tokens. The lexical embedder's thresholds depend on this exactly."""
    words = _WORD_RE.findall(normalise_text(text))
    if drop_stopwords:
        return [w for w in words if w not in _STOPWORDS and len(w) > 1]
    return words


# Ordered alternation, first match wins. Numbers with units, decimals, ranges,
# percentages, p-values, hyphenated model names and identifiers are each kept
# whole before the plain-word rule gets a chance to shred them.
_SCIENCE_TOKEN_RE = re.compile(
    r"""
    (?P<pvalue>   p\s*[<>=]\s*\d*\.?\d+                    )|  # p<0.05
    (?P<compare>  [<>]=?\s*\d+(?:\.\d+)?%?                 )|  # >=0.9, <5%
    (?P<sci>      \d+(?:\.\d+)?\s*[eE]\s*[-+]?\d+          )|  # 1.2e-4
    (?P<power>    \d+(?:\.\d+)?\^[-+]?\d+                  )|  # 10^-4
    (?P<percent>  \d+(?:\.\d+)?%                           )|  # 41.0%
    (?P<citation> \[\s*\d+(?:\s*,\s*\d+)*\s*\]             )|  # [12, 13]
    (?P<ident>    [a-z]+(?:[-_][a-z0-9]+)+                 )|  # bleu-4, bert-base
    (?P<numunit>  \d+(?:\.\d+)?\s*(?:[kmbgt]|ms|s|m|h|gb|mb|kb|b|px|pt|hz|khz|ghz|nm|um|mm|cm|km|mg|kg|ml|l|k)\b )|
    (?P<decimal>  \d+\.\d+                                 )|  # 41.0
    (?P<word>     [a-z]+                                   )|
    (?P<integer>  \d+                                      )
    """,
    re.VERBOSE,
)


def science_tokens(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Tokens for BM25 over paper text, preserving numeric and coded terms.

    Whitespace inside a matched token is squeezed out so that "p < 0.05" and
    "p<0.05" produce the same term. Without that the query and the document
    tokenise differently and the match never happens, which is the whole point
    of having a lexical leg alongside the dense one.
    """
    out: list[str] = []
    for match in _SCIENCE_TOKEN_RE.finditer(normalise_text(text)):
        token = _WS_RE.sub("", match.group(0))
        if drop_stopwords and token in _PAPER_STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        out.append(token)
    return out


def content_hash(text: str) -> str:
    return hashlib.sha256(normalise_text(strip_html(text)).encode("utf-8")).hexdigest()


def file_hash(data: bytes) -> str:
    """Content address for an uploaded PDF, so re-uploading reuses the parse."""
    return hashlib.sha256(data).hexdigest()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_length: int = 72) -> str:
    slug = _SLUG_RE.sub("-", normalise_text(text)).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0]
    return slug or "item"


def truncate(text: str, limit: int, *, suffix: str = "...") -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - len(suffix))]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + suffix


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


# Ligatures a PDF text layer emits as single glyphs, and the quote characters
# that come with them. Left in place they corrupt both the BM25 index and the
# text shown in the UI: "efficient" extracted as "e<fi>cient" matches nothing.
_PDF_FIXES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "−": "-", "ˆ": "^", "…": "...",
    # Every space that is not a space. LaTeX sets a thin or narrow no-break
    # space between a number and its unit, so "257.5 KB" arrives with U+202F
    # in the middle. Left alone the tokeniser reads it as one token, the number
    # never matches a query for it, and it renders as mojibake anywhere the
    # encoding is not perfect.
    " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", "　": " ",
    # Zero width characters, which are invisible and still break a match.
    "​": "", "‌": "", "‍": "", "﻿": "",
}
_HYPHEN_BREAK_RE = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")
_SOFT_WRAP_RE = re.compile(r"(?<![.!?:;])\n(?![\n•*\-\d])")


def clean_pdf_text(text: str) -> str:
    """Undo the damage a PDF text layer does to words.

    Hyphenated line breaks are rejoined before soft wraps are flattened, in
    that order, because doing it the other way round leaves a hyphen stranded
    mid word with no newline left to identify it.
    """
    if not text:
        return ""
    for bad, good in _PDF_FIXES.items():
        text = text.replace(bad, good)
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)
    text = _SOFT_WRAP_RE.sub(" ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


# -------------------------------------------------------------------- JSON


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: Any, default: Any = None) -> Any:
    """Read a JSON column that may already be decoded.

    Postgres hands back a JSONB column as a Python object while SQLite hands
    back the text, so both shapes reach the callers and both are accepted.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
