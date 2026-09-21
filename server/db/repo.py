"""Every query the application runs, in one place.

Nothing above this module writes SQL. That is what keeps the two dialects
working: a query written here is checked against both, and a query written in a
route is checked against whichever one the author happened to be running.

Two conventions from engine.py apply to everything here. Placeholders are `?`
and are rewritten for Postgres, so a literal `%` must never appear in query
text and a LIKE wildcard belongs in the bound parameter. Timestamps are ISO
8601 UTC strings.
"""
from __future__ import annotations

from typing import Any, Sequence

from ..util import dumps, loads, now_iso, slugify
from .engine import Row, get_db


def _json(value: Any, default: Any = None) -> Any:
    return loads(value, default if default is not None else [])


# ------------------------------------------------------------- collections


def create_collection(name: str, description: str = "") -> Row:
    db = get_db()
    stamp = now_iso()
    base = slugify(name) or "collection"
    slug = base
    suffix = 2
    while db.query_one("SELECT id FROM collections WHERE slug = ?", (slug,)):
        slug = f"{base}-{suffix}"
        suffix += 1
    new_id = db.insert(
        "INSERT INTO collections (name, slug, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, slug, description, stamp, stamp),
    )
    return get_collection(new_id)


def get_collection(collection_id: int) -> Row | None:
    return get_db().query_one("SELECT * FROM collections WHERE id = ?", (collection_id,))


def get_collection_by_slug(slug: str) -> Row | None:
    return get_db().query_one("SELECT * FROM collections WHERE slug = ?", (slug,))


def list_collections() -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM papers p WHERE p.collection_id = c.id) AS paper_count "
        "FROM collections c ORDER BY c.updated_at DESC"
    )
    return [dict(r) for r in rows]


def default_collection() -> Row:
    """The collection a paper lands in when the caller names none."""
    existing = get_db().query_one("SELECT * FROM collections ORDER BY id LIMIT 1")
    return existing or create_collection("Library", "Everything uploaded so far")


def delete_collection(collection_id: int) -> None:
    db = get_db()
    for paper in db.query("SELECT id FROM papers WHERE collection_id = ?", (collection_id,)):
        delete_paper(int(paper["id"]))
    db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))


# ------------------------------------------------------------------ papers

# Every column except the two index blobs, which are megabytes and are wanted
# only by the search path. Selecting * for a list of papers would move the
# whole corpus over the wire to render a list of titles.
_PAPER_COLUMNS = (
    "id, collection_id, file_hash, filename, blob_digest, title, title_source, "
    "authors, abstract, doi, arxiv_id, year, venue, keywords, num_pages, toc, "
    "pages, status, status_detail, parser, parser_version, parse_seconds, "
    "warnings, counts, dense_signature, chunk_tokens, chunk_overlap, "
    "indexed_at, read_state, verdict, source_url, quality, created_at, updated_at"
)


def _paper_out(row: Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    paper = dict(row)
    for field in ("authors", "keywords", "warnings", "toc", "pages"):
        paper[field] = _json(paper.get(field), [])
    paper["counts"] = _json(paper.get("counts"), {}) or {}
    paper["quality"] = _json(paper.get("quality"), None)
    paper.pop("lexical_index", None)
    paper.pop("dense_index", None)
    return paper


def create_paper(
    *,
    file_hash: str,
    filename: str,
    blob_digest: str,
    collection_id: int | None = None,
    title: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    db = get_db()
    stamp = now_iso()
    if collection_id is None:
        collection_id = int(default_collection()["id"])
    new_id = db.insert(
        "INSERT INTO papers (collection_id, file_hash, filename, blob_digest, title, "
        "source_url, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (collection_id, file_hash, filename, blob_digest, title, source_url, stamp, stamp),
    )
    return get_paper(new_id)


def get_paper(paper_id: int) -> dict[str, Any] | None:
    return _paper_out(
        get_db().query_one(f"SELECT {_PAPER_COLUMNS} FROM papers WHERE id = ?", (paper_id,))
    )


def get_paper_by_hash(file_hash: str) -> dict[str, Any] | None:
    return _paper_out(
        get_db().query_one(
            f"SELECT {_PAPER_COLUMNS} FROM papers WHERE file_hash = ?", (file_hash,)
        )
    )


def list_papers(
    collection_id: int | None = None,
    *,
    query: str = "",
    status: str = "",
    read_state: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = f"SELECT {_PAPER_COLUMNS} FROM papers WHERE 1 = 1"
    params: list[Any] = []
    if collection_id is not None:
        sql += " AND collection_id = ?"
        params.append(collection_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if read_state:
        sql += " AND read_state = ?"
        params.append(read_state)
    if query:
        sql += " AND (LOWER(title) LIKE ? OR LOWER(abstract) LIKE ? OR LOWER(filename) LIKE ?)"
        needle = f"%{query.lower()}%"
        params.extend([needle, needle, needle])
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [_paper_out(r) for r in get_db().query(sql, params)]


def update_paper(paper_id: int, **fields: Any) -> None:
    if not fields:
        return
    encoded: dict[str, Any] = {}
    for key, value in fields.items():
        encoded[key] = dumps(value) if isinstance(value, (list, dict)) else value
    encoded["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in encoded)
    get_db().execute(
        f"UPDATE papers SET {assignments} WHERE id = ?",
        [*encoded.values(), paper_id],
    )


READ_STATES = ("unread", "reading", "read", "rejected")


def set_triage(paper_id: int, *, read_state: str | None = None, verdict: str | None = None) -> None:
    """Record what you decided about a paper.

    The verdict is deliberately one free text line rather than a rating: the
    useful thing to remember about a paper is the sentence you would say to a
    colleague, and no number carries that.
    """
    fields: dict[str, Any] = {}
    if read_state is not None:
        if read_state not in READ_STATES:
            raise ValueError(f"read_state must be one of {', '.join(READ_STATES)}.")
        fields["read_state"] = read_state
    if verdict is not None:
        fields["verdict"] = verdict[:600]
    if fields:
        update_paper(paper_id, **fields)


def set_status(paper_id: int, status: str, detail: str = "") -> None:
    update_paper(paper_id, status=status, status_detail=detail)


def delete_paper(paper_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM elements WHERE paper_id = ?", (paper_id,))
    db.execute("DELETE FROM chunks WHERE paper_id = ?", (paper_id,))
    db.execute("DELETE FROM analyses WHERE paper_id = ?", (paper_id,))
    db.execute("DELETE FROM usage_log WHERE paper_id = ?", (paper_id,))
    db.execute("DELETE FROM notes WHERE paper_id = ?", (paper_id,))
    db.execute("DELETE FROM papers WHERE id = ?", (paper_id,))


# ---------------------------------------------------------------- elements


def replace_elements(paper_id: int, elements: Sequence[dict[str, Any]]) -> None:
    """Swap in a fresh set of elements for a paper, atomically.

    A re-parse must not leave the old and the new interleaved, which is what a
    delete followed by a separate insert produces if the process dies between
    them.
    """
    db = get_db()
    with db.transaction() as tx:
        tx.execute("DELETE FROM elements WHERE paper_id = ?", (paper_id,))
        for element in elements:
            tx.execute(
                "INSERT INTO elements (paper_id, kind, text, search_text, page, bbox, "
                "ord, section, section_path, level, label, caption, table_data, "
                "image_digest, image_width, image_height, ocr_text, description, "
                "linked_id, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    paper_id,
                    element["kind"],
                    element.get("text", ""),
                    element.get("search_text", ""),
                    element.get("page", 0),
                    dumps(element.get("bbox")) if element.get("bbox") else None,
                    element.get("order", 0),
                    element.get("section", "unknown"),
                    dumps(element.get("section_path", [])),
                    element.get("level", 0),
                    element.get("label", ""),
                    element.get("caption", ""),
                    dumps(element["table"]) if element.get("table") else None,
                    (element.get("image") or {}).get("digest", ""),
                    (element.get("image") or {}).get("width", 0),
                    (element.get("image") or {}).get("height", 0),
                    (element.get("image") or {}).get("ocr_text", ""),
                    (element.get("image") or {}).get("description", ""),
                    element.get("linked_id", ""),
                    dumps(element.get("extra", {})),
                ),
            )


def _element_out(row: Row) -> dict[str, Any]:
    element = dict(row)
    element["bbox"] = _json(element.get("bbox"), None)
    element["section_path"] = _json(element.get("section_path"), [])
    element["table"] = _json(element.get("table_data"), None)
    element["extra"] = _json(element.get("extra"), {}) or {}
    element.pop("table_data", None)
    element["image"] = (
        {
            "digest": element.get("image_digest") or "",
            "width": element.get("image_width") or 0,
            "height": element.get("image_height") or 0,
            "ocr_text": element.get("ocr_text") or "",
            "description": element.get("description") or "",
        }
        if element.get("image_digest")
        else None
    )
    return element


def list_elements(
    paper_id: int,
    *,
    kinds: Sequence[str] = (),
    sections: Sequence[str] = (),
    page: int | None = None,
    limit: int = 5000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM elements WHERE paper_id = ?"
    params: list[Any] = [paper_id]
    if kinds:
        sql += " AND kind IN (" + ", ".join("?" for _ in kinds) + ")"
        params.extend(kinds)
    if sections:
        sql += " AND section IN (" + ", ".join("?" for _ in sections) + ")"
        params.extend(sections)
    if page is not None:
        sql += " AND page = ?"
        params.append(page)
    sql += " ORDER BY ord LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [_element_out(r) for r in get_db().query(sql, params)]


def get_elements_by_ids(paper_id: int, ids: Sequence[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = get_db().query(
        f"SELECT * FROM elements WHERE paper_id = ? AND id IN ({placeholders}) ORDER BY ord",
        [paper_id, *ids],
    )
    return [_element_out(r) for r in rows]


def element_counts(paper_id: int) -> dict[str, int]:
    rows = get_db().query(
        "SELECT kind, COUNT(*) AS n FROM elements WHERE paper_id = ? GROUP BY kind",
        (paper_id,),
    )
    return {str(r["kind"]): int(r["n"]) for r in rows}


def section_counts(paper_id: int) -> dict[str, int]:
    rows = get_db().query(
        "SELECT section, COUNT(*) AS n FROM elements WHERE paper_id = ? GROUP BY section",
        (paper_id,),
    )
    return {str(r["section"]): int(r["n"]) for r in rows}


def search_elements(
    paper_ids: Sequence[int], needle: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    """Substring search across every element of the given papers.

    This backs the universal search in the All Views screens, which is a
    different thing from retrieval: it is exhaustive and literal, so a reader
    looking for every place a term appears gets every place, not the top few by
    relevance. Retrieval ranks; this finds.
    """
    if not paper_ids or not needle.strip():
        return []
    placeholders = ", ".join("?" for _ in paper_ids)
    rows = get_db().query(
        f"SELECT * FROM elements WHERE paper_id IN ({placeholders}) "
        "AND LOWER(search_text) LIKE ? ORDER BY paper_id, ord LIMIT ?",
        [*paper_ids, f"%{needle.lower()}%", limit],
    )
    return [_element_out(r) for r in rows]


# ------------------------------------------------------------------ chunks


def replace_chunks(paper_id: int, chunks: Sequence[dict[str, Any]]) -> list[int]:
    db = get_db()
    with db.transaction() as tx:
        tx.execute("DELETE FROM chunks WHERE paper_id = ?", (paper_id,))
        for chunk in chunks:
            tx.execute(
                "INSERT INTO chunks (paper_id, ord, kind, text, display_text, page, "
                "section, section_path, label, caption, element_ids, token_estimate, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    paper_id,
                    chunk.get("order", 0),
                    chunk.get("kind", "text"),
                    chunk["text"],
                    chunk.get("display_text", ""),
                    chunk.get("page", 0),
                    chunk.get("section", "unknown"),
                    dumps(chunk.get("section_path", [])),
                    chunk.get("label", ""),
                    chunk.get("caption", ""),
                    dumps(chunk.get("element_ids", [])),
                    chunk.get("token_estimate", 0),
                    dumps(chunk.get("extra", {})),
                ),
            )
    return [int(r["id"]) for r in db.query(
        "SELECT id FROM chunks WHERE paper_id = ? ORDER BY ord", (paper_id,)
    )]


def _chunk_out(row: Row) -> dict[str, Any]:
    chunk = dict(row)
    chunk["section_path"] = _json(chunk.get("section_path"), [])
    chunk["element_ids"] = _json(chunk.get("element_ids"), [])
    chunk["extra"] = _json(chunk.get("extra"), {}) or {}
    return chunk


def list_chunks(paper_id: int, *, limit: int = 20000) -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT * FROM chunks WHERE paper_id = ? ORDER BY ord LIMIT ?", (paper_id, limit)
    )
    return [_chunk_out(r) for r in rows]


def get_chunks_by_ids(ids: Sequence[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = get_db().query(f"SELECT * FROM chunks WHERE id IN ({placeholders})", list(ids))
    by_id = {int(r["id"]): _chunk_out(r) for r in rows}
    # Preserve the caller's ranking rather than the database's arbitrary order.
    return [by_id[int(i)] for i in ids if int(i) in by_id]


# ----------------------------------------------------------------- indexes


def save_indexes(
    paper_id: int,
    *,
    lexical: bytes | None = None,
    dense: bytes | None = None,
    dense_signature: str = "",
    chunk_tokens: int = 0,
    chunk_overlap: int = 0,
) -> None:
    db = get_db()
    db.execute(
        "UPDATE papers SET lexical_index = ?, dense_index = ?, dense_signature = ?, "
        "chunk_tokens = ?, chunk_overlap = ?, indexed_at = ?, updated_at = ? WHERE id = ?",
        (lexical, dense, dense_signature, chunk_tokens, chunk_overlap,
         now_iso(), now_iso(), paper_id),
    )


def load_indexes(paper_id: int) -> tuple[bytes | None, bytes | None, str]:
    row = get_db().query_one(
        "SELECT lexical_index, dense_index, dense_signature FROM papers WHERE id = ?",
        (paper_id,),
    )
    if row is None:
        return None, None, ""
    return (
        _as_bytes(row["lexical_index"]),
        _as_bytes(row["dense_index"]),
        str(row["dense_signature"] or ""),
    )


def _as_bytes(value: Any) -> bytes | None:
    """Coerce a BLOB column to bytes.

    SQLite returns bytes, psycopg returns a memoryview for BYTEA, and a
    memoryview does not survive pickle.loads or np.load unchanged.
    """
    if value is None:
        return None
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    return value


# ----------------------------------------------------------------- threads


def create_thread(
    *, collection_id: int | None, paper_ids: Sequence[int], title: str = ""
) -> dict[str, Any]:
    db = get_db()
    stamp = now_iso()
    primary = int(paper_ids[0]) if paper_ids else None
    new_id = db.insert(
        "INSERT INTO threads (collection_id, paper_id, title, paper_ids, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (collection_id, primary, title, dumps(list(paper_ids)), stamp, stamp),
    )
    return get_thread(new_id)


def get_thread(thread_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if row is None:
        return None
    thread = dict(row)
    thread["paper_ids"] = _json(thread.get("paper_ids"), [])
    return thread


def list_threads(
    *, collection_id: int | None = None, paper_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM threads WHERE 1 = 1"
    params: list[Any] = []
    if collection_id is not None:
        sql += " AND collection_id = ?"
        params.append(collection_id)
    if paper_id is not None:
        sql += " AND paper_id = ?"
        params.append(paper_id)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    out = []
    for row in get_db().query(sql, params):
        thread = dict(row)
        thread["paper_ids"] = _json(thread.get("paper_ids"), [])
        out.append(thread)
    return out


def delete_thread(thread_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
    db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))


def add_message(
    thread_id: int,
    *,
    role: str,
    content: str,
    citations: Any = None,
    lens: str = "",
    provider: str = "",
    model: str = "",
    usage: Any = None,
    retrieval: Any = None,
) -> dict[str, Any]:
    db = get_db()
    stamp = now_iso()
    new_id = db.insert(
        "INSERT INTO messages (thread_id, role, content, citations, lens, provider, "
        "model, usage, retrieval, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (thread_id, role, content, dumps(citations or []), lens, provider, model,
         dumps(usage or {}), dumps(retrieval or {}), stamp),
    )
    db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (stamp, thread_id))
    return get_message(new_id)


def get_message(message_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM messages WHERE id = ?", (message_id,))
    return _message_out(row) if row else None


def list_messages(thread_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT * FROM messages WHERE thread_id = ? ORDER BY id LIMIT ?", (thread_id, limit)
    )
    return [_message_out(r) for r in rows]


def _message_out(row: Row) -> dict[str, Any]:
    message = dict(row)
    message["citations"] = _json(message.get("citations"), [])
    message["usage"] = _json(message.get("usage"), {}) or {}
    message["retrieval"] = _json(message.get("retrieval"), {}) or {}
    return message


# ---------------------------------------------------------------- analyses


def save_analysis(
    paper_id: int,
    lens: str,
    *,
    content: str,
    citations: Any = None,
    provider: str = "",
    model: str = "",
    usage: Any = None,
) -> dict[str, Any]:
    db = get_db()
    stamp = now_iso()
    existing = db.query_one(
        "SELECT id FROM analyses WHERE paper_id = ? AND lens = ?", (paper_id, lens)
    )
    if existing:
        db.execute(
            "UPDATE analyses SET content = ?, citations = ?, provider = ?, model = ?, "
            "usage = ?, created_at = ? WHERE id = ?",
            (content, dumps(citations or []), provider, model, dumps(usage or {}),
             stamp, existing["id"]),
        )
        return get_analysis(paper_id, lens)
    db.insert(
        "INSERT INTO analyses (paper_id, lens, content, citations, provider, model, "
        "usage, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (paper_id, lens, content, dumps(citations or []), provider, model,
         dumps(usage or {}), stamp),
    )
    return get_analysis(paper_id, lens)


def get_analysis(paper_id: int, lens: str) -> dict[str, Any] | None:
    row = get_db().query_one(
        "SELECT * FROM analyses WHERE paper_id = ? AND lens = ?", (paper_id, lens)
    )
    if row is None:
        return None
    analysis = dict(row)
    analysis["citations"] = _json(analysis.get("citations"), [])
    analysis["usage"] = _json(analysis.get("usage"), {}) or {}
    return analysis


def list_analyses(paper_id: int) -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT id, paper_id, lens, provider, model, created_at, "
        "LENGTH(content) AS length FROM analyses WHERE paper_id = ? ORDER BY lens",
        (paper_id,),
    )
    return [dict(r) for r in rows]


def delete_analysis(paper_id: int, lens: str) -> None:
    get_db().execute(
        "DELETE FROM analyses WHERE paper_id = ? AND lens = ?", (paper_id, lens)
    )


def delete_analyses(paper_id: int) -> int:
    """Drop every cached analysis for a paper, and say how many went.

    Called when the chunks a paper is made of change. An analysis cites
    excerpt numbers that resolve to chunk ids, so after a reindex those
    citations point at different text, or at nothing. Serving the cached
    result would show reasoning attached to evidence it was not written from,
    which is worse than making the user run it again.
    """
    db = get_db()
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM analyses WHERE paper_id = ?", (paper_id,)
    )
    db.execute("DELETE FROM analyses WHERE paper_id = ?", (paper_id,))
    return int(row["n"]) if row else 0


# ------------------------------------------------------------------- usage


def record_usage(entries: Sequence[Any], *, paper_id: int | None = None) -> None:
    if not entries:
        return
    stamp = now_iso()
    get_db().execute_many(
        "INSERT INTO usage_log (paper_id, provider, model, kind, operation, "
        "input_tokens, output_tokens, units, cost_usd, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (paper_id, e.provider, e.model, e.kind, e.operation,
             e.input_tokens, e.output_tokens, e.units, e.cost_usd, stamp)
            for e in entries
        ],
    )


def usage_totals(*, paper_id: int | None = None) -> dict[str, Any]:
    sql = (
        "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(cost_usd), 0) AS cost_usd FROM usage_log"
    )
    params: list[Any] = []
    if paper_id is not None:
        sql += " WHERE paper_id = ?"
        params.append(paper_id)
    row = get_db().query_one(sql, params)
    return dict(row) if row else {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0}


def usage_by_model(limit: int = 50) -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT provider, model, kind, COUNT(*) AS calls, "
        "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(cost_usd), 0) AS cost_usd "
        "FROM usage_log GROUP BY provider, model, kind ORDER BY calls DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- notes


NOTE_COLOURS = ("yellow", "green", "blue", "pink", "grey")


def create_note(
    paper_id: int,
    *,
    body: str,
    element_id: int | None = None,
    colour: str = "yellow",
) -> dict[str, Any]:
    """Attach a note to one element, or to the paper as a whole."""
    if not (body or "").strip():
        raise ValueError("A note needs something in it.")
    if colour not in NOTE_COLOURS:
        colour = "yellow"
    db = get_db()
    stamp = now_iso()
    new_id = db.insert(
        "INSERT INTO notes (paper_id, element_id, body, colour, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (paper_id, element_id, body.strip()[:4000], colour, stamp, stamp),
    )
    return get_note(new_id)


def get_note(note_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
    return dict(row) if row else None


def update_note(note_id: int, *, body: str | None = None, colour: str | None = None) -> dict[str, Any] | None:
    fields: dict[str, Any] = {}
    if body is not None:
        fields["body"] = body.strip()[:4000]
    if colour is not None and colour in NOTE_COLOURS:
        fields["colour"] = colour
    if not fields:
        return get_note(note_id)
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    get_db().execute(
        f"UPDATE notes SET {assignments} WHERE id = ?", [*fields.values(), note_id]
    )
    return get_note(note_id)


def delete_note(note_id: int) -> None:
    get_db().execute("DELETE FROM notes WHERE id = ?", (note_id,))


def list_notes(paper_id: int | None = None, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Notes, newest last, with the element they point at if there is one.

    The element's page and label are joined in so a note can be listed and
    located without a second query per note.
    """
    sql = (
        "SELECT n.*, e.page AS element_page, e.kind AS element_kind, "
        "e.label AS element_label, e.text AS element_text, p.title AS paper_title "
        "FROM notes n "
        "LEFT JOIN elements e ON e.id = n.element_id "
        "LEFT JOIN papers p ON p.id = n.paper_id "
    )
    params: list[Any] = []
    if paper_id is not None:
        sql += "WHERE n.paper_id = ? "
        params.append(paper_id)
    sql += "ORDER BY n.created_at LIMIT ?"
    params.append(limit)

    out: list[dict[str, Any]] = []
    for row in get_db().query(sql, params):
        note = dict(row)
        # A long element is trimmed here rather than in the browser, because
        # the note list on a big paper would otherwise carry the whole paper.
        if note.get("element_text"):
            note["element_text"] = str(note["element_text"])[:280]
        out.append(note)
    return out


def note_counts(paper_id: int) -> dict[int, int]:
    """Notes per element, so the element list can show which ones are annotated."""
    rows = get_db().query(
        "SELECT element_id, COUNT(*) AS n FROM notes "
        "WHERE paper_id = ? AND element_id IS NOT NULL GROUP BY element_id",
        (paper_id,),
    )
    return {int(r["element_id"]): int(r["n"]) for r in rows}
