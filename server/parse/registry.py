"""Choosing a parser, and reporting honestly about the ones that cannot run.

The registry is the only place that knows both tiers exist. Everything
downstream consumes a `ParseResult` and cannot tell which produced it, which is
what lets a paper parsed deeply on a laptop be served from a serverless
deployment that could never have parsed it itself.

`resolve()` honours an explicit choice even when that parser is unavailable, so
the caller gets a specific error naming what is missing rather than being
silently downgraded. Only "auto" falls back, and when it does it says so.
"""
from __future__ import annotations

from typing import Any

from .. import settings
from .base import BaseParser, ParseError, ParseResult
from .deep import DeepParser
from .fast import FastParser

_ORDER: tuple[type[BaseParser], ...] = (DeepParser, FastParser)

_registry: dict[str, BaseParser] | None = None


def parser_map() -> dict[str, BaseParser]:
    global _registry
    if _registry is None:
        _registry = {cls().id: cls() for cls in _ORDER}
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None


def all_status() -> list[dict[str, Any]]:
    return [p.status() for p in parser_map().values()]


def available() -> list[BaseParser]:
    return [p for p in parser_map().values() if p.is_available()]


def resolve(mode: str | None = None) -> BaseParser:
    """Pick a parser. "auto" takes the best usable one, anything else is exact."""
    chosen = (mode or settings.PARSE_MODE or "auto").strip().lower()

    if chosen and chosen != "auto":
        parser = parser_map().get(chosen)
        if parser is None:
            known = ", ".join(sorted(parser_map()))
            raise ParseError(
                f"No parser called {chosen!r}.",
                hint=f"Choose one of: {known}, or auto.",
            )
        if not parser.is_available():
            raise ParseError(
                f"{parser.label} cannot run here.",
                parser=parser.id,
                hint=parser.unavailable_reason(),
            )
        return parser

    usable = sorted(available(), key=lambda p: p.quality, reverse=True)
    if usable:
        return usable[0]

    reasons = "; ".join(f"{p.label}: {p.unavailable_reason()}" for p in parser_map().values())
    raise ParseError(
        "No PDF parser is available in this environment.",
        hint=reasons or "Run: pip install -r requirements.txt",
    )


def parse(pdf_path: str, *, mode: str | None = None, log=None) -> ParseResult:
    """Parse a PDF, falling back to the fast tier if the deep one fails.

    A deep parse can fail for reasons that have nothing to do with the PDF: a
    model download interrupted, a machine that ran out of memory partway
    through. Falling back means the user gets a usable paper and a warning
    rather than an error page, and the warning travels with the result so the
    difference is visible rather than hidden.
    """
    parser = resolve(mode)
    try:
        return parser.parse(pdf_path, log=log)
    except ParseError:
        if parser.id == "fast" or (mode or "auto").lower() != "auto":
            raise
        fallback = parser_map()["fast"]
        if not fallback.is_available():
            raise
        if log:
            log("Deep parsing failed, falling back to the fast parser")
        result = fallback.parse(pdf_path, log=log)
        result.warnings.insert(
            0,
            "Deep parsing failed on this machine, so this paper was parsed with "
            "the fast parser. Tables with no ruling lines and vector figures may "
            "be less accurate. Re-run the parse to try again.",
        )
        return result
