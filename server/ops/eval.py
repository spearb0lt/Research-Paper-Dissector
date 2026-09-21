"""Regression eval for the fast parser, against hand checked ground truth.

The constants in parse/tables.py were chosen by sweeping them against the five
tables below. Nothing in this file is clever: it is a record of what was
measured by eye on the rendered PDFs, so that a later change to the column
detector cannot quietly undo it. Every one of the bugs this eval encodes was
real and shipped at some point during the build:

* The text strategy in both pdfplumber and PyMuPDF turned page 3 of the
  Transformer paper into a 22 by 8 table of word fragments. Seventeen tables
  were "found" in a paper that has four.
* A caption set in body font absorbed the table below it, giving the caption a
  bounding box that overlapped the table it was labelling, which broke caption
  to table linking on every results page.
* The vector figure crop was taken from the top of the page because the ceiling
  search could not see figures already extracted, producing a duplicate of
  every figure that also had an embedded image.

Run it with: python -m server.ops.eval
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from ..parse.base import Kind
from ..parse.fast import FastParser

SAMPLES = Path(__file__).resolve().parent.parent.parent / "data" / "samples"

# arXiv ids, fetched on first run so the eval is self contained.
PAPERS = {
    "attention": "1706.03762",
    "colpali": "2407.01449",
    "docling": "2501.17887",
}

# (paper, page) -> expected column count, read off the rendered PDF by eye.
TABLE_COLUMNS = {
    ("attention", 6): 4,    # Table 1, layer type comparison
    ("attention", 8): 5,    # Table 2, BLEU and training cost
    ("attention", 9): 13,   # Table 3, the ablation grid
    ("attention", 10): 3,   # Table 4, constituency parsing
    ("docling", 5): 5,      # Table 1, versions and configuration
}

# How many tables each paper actually contains, counted by hand. The point of
# this check is precision, not recall: finding five tables in a paper that has
# four means body text is being shredded again.
TABLE_TOTALS = {"attention": 4, "colpali": 8, "docling": 3}

# The fast tier does not find every table, and that is the documented reason
# the deep tier exists. This is the floor below which it is broken rather than
# merely limited.
MIN_TABLE_RECALL = 0.30

# Every table on these pages carries a caption in the PDF, so a failure to link
# one is a bug in the pairing, not a property of the paper.
CAPTIONED = {("attention", 6), ("attention", 8), ("attention", 9),
             ("attention", 10), ("docling", 5)}

# Display equations the fast tier must find, and the count it must not exceed.
# The Transformer paper has exactly four numbered display equations. The first
# version of the detector found 27, because author lines carry a footnote
# asterisk from a maths face and the attention visualisation pages are full of
# "<pad>" and "<EOS>", all of which are set in no body font.
FORMULA_EXPECTED = {"attention": (4, 4)}
FORMULA_MUST_CONTAIN = {
    "attention": ["Attention(Q", "MultiHead(Q", "FFN(x)"],
}

TITLES = {
    "attention": "Attention Is All You Need",
    "colpali": "ColPali",   # prefix match, the full title is long and cased oddly
}


def _ensure(name: str, arxiv_id: str) -> Path:
    path = SAMPLES / f"{name}.pdf"
    if path.exists() and path.stat().st_size > 10_000:
        return path
    SAMPLES.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {name} from arXiv...")
    urllib.request.urlretrieve(f"https://arxiv.org/pdf/{arxiv_id}", path)
    return path


def run() -> int:
    parser = FastParser()
    if not parser.is_available():
        print(f"FAIL: the fast parser is unavailable: {parser.unavailable_reason()}")
        return 1

    failures: list[str] = []
    results = {}
    for name, arxiv_id in PAPERS.items():
        path = _ensure(name, arxiv_id)
        result = parser.parse(str(path))
        results[name] = result
        tables = [e for e in result.elements if e.kind is Kind.TABLE]
        figures = [e for e in result.elements if e.kind is Kind.FIGURE]
        print(
            f"{name:10} {result.duration_seconds:5.1f}s  "
            f"{len(tables):2d} tables  {len(figures):2d} figures  "
            f"{len(result.elements):4d} elements"
        )

        expected = TABLE_TOTALS[name]
        if len(tables) > expected:
            failures.append(
                f"{name}: found {len(tables)} tables but the paper has {expected}. "
                "Body text is being detected as a table again."
            )
        recall = len(tables) / expected if expected else 1.0
        if recall < MIN_TABLE_RECALL:
            failures.append(
                f"{name}: table recall {recall:.0%} is below the {MIN_TABLE_RECALL:.0%} floor."
            )

        for table in tables:
            key = (name, table.page)
            if key in TABLE_COLUMNS:
                got = table.table.num_cols
                want = TABLE_COLUMNS[key]
                mark = "ok" if got == want else "WRONG"
                print(f"             p{table.page:<3} columns {got:2d} (want {want:2d})  {mark}")
                if got != want:
                    failures.append(
                        f"{name} p{table.page}: {got} columns, expected {want}."
                    )
            if key in CAPTIONED and not table.caption:
                failures.append(f"{name} p{table.page}: table has no linked caption.")

        # A figure recovered both as an embedded image and as a vector crop is
        # the duplication bug. Two figures genuinely overlapping is not a thing
        # a paper does.
        for page in {f.page for f in figures}:
            on_page = [f for f in figures if f.page == page and f.bbox]
            for index, first in enumerate(on_page):
                for second in on_page[index + 1:]:
                    if _overlap(first.bbox, second.bbox) > 0.6:
                        failures.append(
                            f"{name} p{page}: two figures overlap, which is the "
                            "duplicate extraction bug."
                        )
                        break

        formulas = [e for e in result.elements if e.kind is Kind.FORMULA]
        if name in FORMULA_EXPECTED:
            low, high = FORMULA_EXPECTED[name]
            print(f"             formulas {len(formulas):2d} (want {low} to {high})")
            if not low <= len(formulas) <= high:
                failures.append(
                    f"{name}: found {len(formulas)} display equations, expected "
                    f"{low} to {high}. Author lines and vocabulary tokens are the "
                    "usual false positives."
                )
        for needle in FORMULA_MUST_CONTAIN.get(name, []):
            if not any(needle in f.text for f in formulas):
                failures.append(f"{name}: no display equation containing {needle!r}.")

        if name in TITLES and not result.meta.title.lower().startswith(
            TITLES[name].lower()[:20]
        ):
            failures.append(
                f"{name}: title came out as {result.meta.title[:60]!r}, "
                f"expected it to start with {TITLES[name]!r}."
            )

    abstract = results["attention"].meta.abstract
    if len(abstract) < 200:
        failures.append(f"attention: abstract is {len(abstract)} characters, expected over 200.")

    print()
    print("retrieval (ingesting into a scratch database)")
    try:
        failures.extend(_retrieval_check())
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        failures.append(f"retrieval eval could not run: {exc}")

    print()
    if failures:
        print(f"FAILED with {len(failures)} problem(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASSED")
    return 0


# Retrieval ground truth for "Attention Is All You Need". Each question names
# the page the answer is actually on, and the check is that at least one of
# those pages appears in the top three hits. Page rather than chunk id, because
# a chunk id changes whenever the chunk budget changes and the answer does not.
#
# The questions are chosen to exercise each leg. The first two are lexical: a
# dense encoder cannot tell 41.0 from 41.8. The third and fourth are semantic,
# where the wording of the question shares almost nothing with the wording of
# the answer.
RETRIEVAL_CASES: tuple[tuple[str, set[int]], ...] = (
    ("What BLEU score did the big model achieve on English to French?", {8}),
    ("What is the dropout rate and label smoothing value?", {8}),
    ("What dataset did they train on?", {7}),
    ("Why is self-attention faster than recurrent layers?", {6, 7}),
    ("What optimizer and learning rate schedule were used?", {7}),
    ("What does Figure 1 show?", {3}),
    # An explicit label reference. "figure" matches every figure in the paper
    # and "2" carries almost no signal, so without the label pin this returned
    # other figures and a correctly grounded model then reported that Table 2
    # was not among the excerpts.
    ("What is in Table 2?", {8}),
    ("Describe Figure 2", {4}),
)


def run_retrieval(paper_id: int) -> list[str]:
    """Check that retrieval finds the right page, and that both legs fuse.

    The fusion check is not incidental. The dense index once serialised its
    chunk ids as strings while the lexical index used integers, so rank fusion
    saw them as different documents and never merged a single result. Hybrid
    search silently ran as two independent single leg searches, which is
    invisible in the output and halves the quality. A test that only looked at
    whether the right page came back would have passed.
    """
    from ..ask import retrieve

    failures: list[str] = []
    fused_any = False
    for question, pages in RETRIEVAL_CASES:
        found = retrieve.search([paper_id], question, top_k=3)
        hits = [h.as_dict() for h in found.hits]
        got = [h["page"] for h in hits]
        ok = bool(pages & set(got))
        print(f"  {'ok   ' if ok else 'MISS '} p{sorted(pages)} got p{got}  {question[:52]}")
        if not ok:
            failures.append(
                f"retrieval: {question!r} expected a hit on page(s) {sorted(pages)}, got {got}."
            )
        if any(len(h["legs"]) > 1 for h in hits):
            fused_any = True

    for question, pages in (("What is in Table 2?", {8}), ("Describe Figure 2", {4})):
        found = retrieve.search([paper_id], question, top_k=3)
        top = found.hits[0].as_dict() if found.hits else {}
        if "label" not in (top.get("legs") or {}):
            failures.append(
                f"retrieval: {question!r} did not pin the element it names to "
                "rank one. Label references must be looked up, not ranked."
            )
        elif top.get("page") not in pages:
            failures.append(
                f"retrieval: {question!r} pinned page {top.get('page')}, expected {sorted(pages)}."
            )

    if not fused_any:
        failures.append(
            "retrieval: no result was found by more than one leg. The lexical and "
            "dense indexes are not fusing, which usually means their document ids "
            "have different types."
        )
    return failures


def _retrieval_check() -> list[str]:
    """Ingest into a throwaway database so the eval never touches real data."""
    import os
    import tempfile

    from .. import settings
    from ..db import engine

    with tempfile.TemporaryDirectory() as scratch:
        original = settings.SQLITE_PATH
        settings.SQLITE_PATH = __import__("pathlib").Path(scratch) / "eval.db"
        os.environ.pop("DATABASE_URL", None)
        engine.reset_db()
        try:
            from .. import pipeline

            data = (SAMPLES / "attention.pdf").read_bytes()
            result = pipeline.ingest(data, "attention.pdf")
            if not result.dense_enabled:
                return [
                    "retrieval: the dense index did not build, so only one leg ran."
                ]
            return run_retrieval(result.paper_id)
        finally:
            settings.SQLITE_PATH = original
            engine.reset_db()


def _overlap(a, b) -> float:
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min(a.width * a.height, b.width * b.height)
    return (width * height) / smaller if smaller else 0.0


if __name__ == "__main__":
    sys.exit(run())
