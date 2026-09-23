"""Everything the app does, from a terminal.

The point is not convenience. It is that the command line and the API call the
same functions, so a bug found through one is fixed for both, and a parse can
be run on a machine that can do it and served from one that cannot.

    python -m server.ops.cli doctor
    python -m server.ops.cli add paper.pdf --deep --ocr
    python -m server.ops.cli list
    python -m server.ops.cli search 1 "what was the BLEU score"
    python -m server.ops.cli ask 1 "what are the limitations" --lens limitations
    python -m server.ops.cli reindex 1 --no-dense
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import blobs, settings
from ..db import repo
from ..db.engine import get_db
from ..embeddings import registry as embeddings
from ..llm import registry as llm
from ..parse import registry as parsers
from ..parse.base import ParseError
from ..runtime import current as runtime


def _print(*parts: object) -> None:
    """Write a line that a Windows console can actually encode.

    A terminal on Windows defaults to a code page that cannot represent much of
    what comes out of a paper, and an unencodable character turns a working
    report into a UnicodeEncodeError.
    """
    text = " ".join(str(p) for p in parts)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")


def doctor(_: argparse.Namespace) -> int:
    """Report what this machine can do, and what it cannot and why."""
    current = runtime()
    _print(f"{settings.APP_NAME}")
    _print(f"  tier        {current.tier.value} on {current.platform}")
    _print(f"  python      {current.python_version}")
    _print(f"  data dir    {settings.DATA_DIR}")
    _print(f"  blobs       {settings.BLOB_DIR} ({blobs.usage_bytes() / 1e6:.1f} MB)")

    _print("\ncapabilities")
    for capability in current.capabilities:
        mark = "yes" if capability.available else "no "
        _print(f"  [{mark}] {capability.label}")
        if not capability.available and capability.reason:
            _print(f"        {capability.reason}")

    _print("\nparsers")
    for status in parsers.all_status():
        mark = "yes" if status["available"] else "no "
        _print(f"  [{mark}] {status['label']}")
        if not status["available"]:
            _print(f"        {status['reason']}")

    _print("\nembeddings")
    for status in embeddings.all_status():
        mark = "yes" if status["available"] else "no "
        _print(f"  [{mark}] {status['id']:22} {status['label']}")
    try:
        chosen = embeddings.resolve()
        _print(f"  -> using {chosen.id} ({chosen.model_id()}, {chosen.dim}d)")
    except Exception as exc:  # noqa: BLE001
        _print(f"  -> none usable: {exc}")

    _print("\nmodel providers")
    available = [s for s in llm.all_status() if s["available"]]
    if not available:
        _print("  none configured. Retrieval, browsing and search all still work.")
    for status in available:
        _print(f"  [yes] {status['id']:14} {status['label']} ({status['key_source']} key)")

    _print("\ndatabase")
    try:
        get_db().ensure_schema()
        papers = repo.list_papers(limit=1000)
        _print(f"  ok, {len(papers)} paper(s), dialect {get_db().dialect}")
    except Exception as exc:  # noqa: BLE001 - this is the thing being reported
        _print(f"  FAILED: {exc}")
        return 1
    return 0


def add(args: argparse.Namespace) -> int:
    from .. import pipeline

    path = Path(args.path)
    if not path.exists():
        _print(f"No such file: {path}")
        return 1

    mode = "deep" if args.deep else ("fast" if args.fast else None)
    try:
        result = pipeline.ingest(
            path.read_bytes(),
            path.name,
            parse_mode=mode,
            dense=not args.no_dense,
            ocr=args.ocr,
            force=args.force,
            progress=lambda stage, message, fraction: _print(
                f"  [{stage:6}] {fraction * 100:3.0f}%  {message}"
            ),
        )
    except ParseError as exc:
        _print(f"Failed: {exc.message}")
        if exc.hint:
            _print(f"  {exc.hint}")
        return 1

    paper = repo.get_paper(result.paper_id)
    _print(f"\n#{result.paper_id}  {paper['title'] if paper else ''}")
    _print(
        f"  {result.element_count} elements, {result.chunk_count} chunks, "
        f"parse {result.parse_seconds:.1f}s, index {result.index_seconds:.1f}s"
    )
    for warning in result.warnings:
        _print(f"  warning: {warning}")
    return 0


def list_papers(_: argparse.Namespace) -> int:
    papers = repo.list_papers(limit=500)
    if not papers:
        _print("The library is empty.")
        return 0
    for paper in papers:
        counts = paper.get("counts") or {}
        _print(
            f"#{paper['id']:<4} {paper['status']:<9} "
            f"{(paper['title'] or paper['filename'])[:62]:<62} "
            f"{paper['num_pages']:>3}p "
            f"{counts.get('figure', 0):>3}fig {counts.get('table', 0):>3}tab"
        )
    return 0


def search(args: argparse.Namespace) -> int:
    from ..ask import retrieve

    found = retrieve.search(args.paper_ids, args.query, top_k=args.top_k)
    if not found.hits:
        _print("Nothing matched.")
        for leg, reason in found.legs_skipped.items():
            _print(f"  {leg} skipped: {reason}")
        return 0

    _print(f"legs {found.legs_used}  weights " + ", ".join(
        f"{k}={v:.2f}" for k, v in found.weights.items()
    ))
    for hit in found.hits:
        data = hit.as_dict()
        legs = " ".join(f"{leg}#{info['rank']}" for leg, info in data["legs"].items())
        _print(
            f"\n[{data['score']:.4f}] {data['kind']:6} p{data['page']:<3} "
            f"{data['section']:12} {legs}"
        )
        _print(f"  {data['text'][:300]}")
    return 0


def ask(args: argparse.Namespace) -> int:
    from ..ask import answer

    result = answer.ask(
        args.paper_ids,
        args.question,
        lens=args.lens,
        use_llm=not args.no_llm,
        provider=args.provider,
        model=args.model,
    )
    if result.note:
        _print(f"({result.note})\n")
    if result.text:
        _print(result.text)
        _print(f"\n-- {result.provider}/{result.model}, {result.usage}")
    _print("\nevidence:")
    for citation in result.citations:
        _print(
            f"  [{citation['n']}] {citation['kind']:6} p{citation['page']:<3} "
            f"{citation['section']:12} {citation['label']}"
        )
    return 0


def reindex(args: argparse.Namespace) -> int:
    from .. import pipeline

    try:
        count, dense_on = pipeline.reindex(
            args.paper_id,
            dense=not args.no_dense,
            chunk_tokens=args.chunk_tokens,
            chunk_overlap=args.chunk_overlap,
            progress=lambda stage, message, fraction: _print(f"  [{stage}] {message}"),
        )
    except ValueError as exc:
        _print(str(exc))
        return 1
    _print(f"{count} chunks, dense index {'on' if dense_on else 'off'}")
    return 0


def delete(args: argparse.Namespace) -> int:
    paper = repo.get_paper(args.paper_id)
    if paper is None:
        _print("No such paper.")
        return 1
    repo.delete_paper(args.paper_id)
    removed, freed = blobs.collect_orphans()
    _print(f"Removed #{args.paper_id} {paper['title']}")
    if removed:
        _print(f"  freed {removed} blob(s), {freed / 1e6:.1f} MB")
    return 0


def gc(_: argparse.Namespace) -> int:
    """Sweep stored bytes nothing points at any more.

    Worth running on its own after a reindex or a failed parse, both of which
    can leave a crop or a render behind without deleting a paper.
    """
    before = blobs.usage_bytes()
    removed, freed = blobs.collect_orphans()
    _print(f"blobs before   {before / 1e6:.1f} MB")
    _print(f"removed        {removed} orphan(s), {freed / 1e6:.1f} MB")
    _print(f"blobs now      {blobs.usage_bytes() / 1e6:.1f} MB")
    return 0


def evaluate(_: argparse.Namespace) -> int:
    from . import eval as eval_module

    return eval_module.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.ops.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="What this machine can do").set_defaults(fn=doctor)
    sub.add_parser("list", help="Every paper in the library").set_defaults(fn=list_papers)
    sub.add_parser("eval", help="Run the parser and retrieval eval").set_defaults(fn=evaluate)

    add_parser = sub.add_parser("add", help="Ingest a PDF")
    add_parser.add_argument("path")
    add_parser.add_argument("--deep", action="store_true", help="Force the deep parser")
    add_parser.add_argument("--fast", action="store_true", help="Force the fast parser")
    add_parser.add_argument("--ocr", action="store_true", help="Read text inside figures")
    add_parser.add_argument("--no-dense", action="store_true", help="Keyword index only")
    add_parser.add_argument("--force", action="store_true", help="Re-parse even if present")
    add_parser.set_defaults(fn=add)

    search_parser = sub.add_parser("search", help="Retrieval, with no model involved")
    search_parser.add_argument("paper_ids", type=lambda v: [int(p) for p in v.split(",")])
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=8)
    search_parser.set_defaults(fn=search)

    ask_parser = sub.add_parser("ask", help="A cited answer")
    ask_parser.add_argument("paper_ids", type=lambda v: [int(p) for p in v.split(",")])
    ask_parser.add_argument("question", nargs="?", default="")
    ask_parser.add_argument("--lens", default="")
    ask_parser.add_argument("--provider", default=None)
    ask_parser.add_argument("--model", default=None)
    ask_parser.add_argument("--no-llm", action="store_true", help="Evidence only")
    ask_parser.set_defaults(fn=ask)

    reindex_parser = sub.add_parser("reindex", help="Rebuild chunks and indexes")
    reindex_parser.add_argument("paper_id", type=int)
    reindex_parser.add_argument("--no-dense", action="store_true")
    reindex_parser.add_argument("--chunk-tokens", type=int, default=None)
    reindex_parser.add_argument("--chunk-overlap", type=int, default=None)
    reindex_parser.set_defaults(fn=reindex)

    delete_parser = sub.add_parser("delete", help="Remove a paper")
    delete_parser.add_argument("paper_id", type=int)
    delete_parser.set_defaults(fn=delete)

    sub.add_parser(
        "gc", help="Delete stored bytes no paper refers to any more"
    ).set_defaults(fn=gc)

    args = parser.parse_args(argv)
    get_db().ensure_schema()
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
