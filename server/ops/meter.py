"""Records what each model call consumed, without threading it through the code.

Token counts are needed in exactly one place, the run's cost report, but they
are produced in a dozen places scattered across the pipeline. Passing a usage
object through every function signature would distort all of them, so calls
append to a context bound collector instead and the runner drains it at the end
of each stage.

The collector is a context variable, so a background thread or a second
concurrent request gets its own and the totals never mix.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Published prices per million tokens, used only to show an estimate. A model
# that is not listed is reported with a zero cost and a note, rather than a
# guessed number that would read as authoritative.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}


@dataclass
class Entry:
    provider: str
    model: str
    kind: str = "llm"  # llm or embed
    operation: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    units: int = 0  # items embedded, for providers that do not report tokens

    @property
    def cost_usd(self) -> float:
        price = PRICES_PER_MTOK.get(self.model)
        if not price:
            return 0.0
        prompt_price, completion_price = price
        return (
            self.input_tokens * prompt_price + self.output_tokens * completion_price
        ) / 1_000_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "kind": self.kind,
            "operation": self.operation,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "units": self.units,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class Collector:
    entries: list[Entry] = field(default_factory=list)

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)

    def drain(self) -> list[Entry]:
        out = self.entries
        self.entries = []
        return out

    def totals(self) -> dict[str, Any]:
        return {
            "calls": len(self.entries),
            "input_tokens": sum(e.input_tokens for e in self.entries),
            "output_tokens": sum(e.output_tokens for e in self.entries),
            "units": sum(e.units for e in self.entries),
            "cost_usd": round(sum(e.cost_usd for e in self.entries), 6),
        }


_collector: ContextVar[Collector | None] = ContextVar("usage_collector", default=None)


def start() -> Collector:
    collector = Collector()
    _collector.set(collector)
    return collector


def active() -> Collector | None:
    return _collector.get()


def record(
    *,
    provider: str,
    model: str,
    kind: str = "llm",
    operation: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    units: int = 0,
) -> None:
    """Note one call. A no-op when nothing is collecting, so callers need no guard."""
    collector = _collector.get()
    if collector is None:
        return
    collector.add(
        Entry(
            provider=provider,
            model=model,
            kind=kind,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            units=units,
        )
    )


def stop() -> None:
    _collector.set(None)
