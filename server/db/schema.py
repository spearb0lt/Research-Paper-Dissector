"""The schema, written once in the portable dialect from engine.py.

The shape follows one decision: the element is the unit of everything. A text
block, a table, a figure, a formula and a reference entry are all rows in
`elements`, distinguished by `kind`. That is what lets the All Views screens,
retrieval, citation and the reader overlay all read the same rows, and what
makes a citation able to point at a bounding box on a page rather than at a
chunk of text that has lost its origin.

`chunks` is derived from `elements` and is disposable: re-chunking a paper with
a different token budget deletes and rebuilds it without touching the parse.
The two indexes are blobs on `papers` rather than tables, because at a few
thousand vectors they are read whole on every search and never queried
row by row, so storing them as rows would be all cost and no benefit.

Portable type tokens: {PK} {JSON} {BLOB} {BOOL} {TS} {TEXT} {INT} {REAL}
"""
from __future__ import annotations

from .engine import render_ddl

TABLES: tuple[str, ...] = (
    # ------------------------------------------------------------ collections
    """
    CREATE TABLE IF NOT EXISTS collections (
        id           {PK},
        name         {TEXT} NOT NULL,
        slug         {TEXT} NOT NULL UNIQUE,
        description  {TEXT} DEFAULT '',
        created_at   {TS}   NOT NULL,
        updated_at   {TS}   NOT NULL
    )
    """,
    # ----------------------------------------------------------------- papers
    """
    CREATE TABLE IF NOT EXISTS papers (
        id              {PK},
        collection_id   {INT},
        -- SHA-256 of the uploaded bytes. Re-uploading the same PDF finds the
        -- existing row instead of parsing it a second time.
        file_hash       {TEXT} NOT NULL,
        filename        {TEXT} DEFAULT '',
        blob_digest     {TEXT} DEFAULT '',
        title           {TEXT} DEFAULT '',
        title_source    {TEXT} DEFAULT '',
        authors         {JSON},
        abstract        {TEXT} DEFAULT '',
        doi             {TEXT} DEFAULT '',
        arxiv_id        {TEXT} DEFAULT '',
        year            {INT}  DEFAULT 0,
        venue           {TEXT} DEFAULT '',
        keywords        {JSON},
        num_pages       {INT}  DEFAULT 0,
        toc             {JSON},
        pages           {JSON},
        -- pending, parsing, indexing, ready, failed. A paper is browsable as
        -- soon as it is parsed, so the UI shows elements while the index is
        -- still being built rather than a spinner over the whole paper.
        status          {TEXT} DEFAULT 'pending',
        status_detail   {TEXT} DEFAULT '',
        parser          {TEXT} DEFAULT '',
        parser_version  {TEXT} DEFAULT '',
        parse_seconds   {REAL} DEFAULT 0,
        warnings        {JSON},
        counts          {JSON},
        -- Retrieval indexes, stored whole because they are read whole.
        lexical_index   {BLOB},
        dense_index     {BLOB},
        -- Which embedding backend built dense_index. A search against a matrix
        -- built by a different one is meaningless, so this is checked and the
        -- index rebuilt rather than silently returning wrong neighbours.
        dense_signature {TEXT} DEFAULT '',
        chunk_tokens    {INT}  DEFAULT 0,
        chunk_overlap   {INT}  DEFAULT 0,
        indexed_at      {TS},
        -- Triage. A library with no read state becomes a junk drawer, and the
        -- verdict is the one line you would tell a colleague about the paper.
        read_state      {TEXT} DEFAULT 'unread',
        verdict         {TEXT} DEFAULT '',
        -- Where this paper came from, when it was fetched rather than uploaded.
        source_url      {TEXT} DEFAULT '',
        -- The parse quality assessment, recomputed whenever elements change.
        quality         {JSON},
        created_at      {TS}   NOT NULL,
        updated_at      {TS}   NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS elements (
        id             {PK},
        paper_id       {INT}  NOT NULL,
        kind           {TEXT} NOT NULL,
        text           {TEXT} DEFAULT '',
        -- Everything about this element a lexical index should see: its label,
        -- caption, body, every table cell, and any OCR text. Denormalised on
        -- purpose so that search reads one column instead of reassembling it.
        search_text    {TEXT} DEFAULT '',
        page           {INT}  DEFAULT 0,
        bbox           {JSON},
        ord            {INT}  DEFAULT 0,
        section        {TEXT} DEFAULT 'unknown',
        section_path   {JSON},
        level          {INT}  DEFAULT 0,
        label          {TEXT} DEFAULT '',
        caption        {TEXT} DEFAULT '',
        table_data     {JSON},
        image_digest   {TEXT} DEFAULT '',
        image_width    {INT}  DEFAULT 0,
        image_height   {INT}  DEFAULT 0,
        ocr_text       {TEXT} DEFAULT '',
        description    {TEXT} DEFAULT '',
        linked_id      {TEXT} DEFAULT '',
        extra          {JSON}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id             {PK},
        paper_id       {INT}  NOT NULL,
        ord            {INT}  DEFAULT 0,
        kind           {TEXT} DEFAULT 'text',
        -- What is embedded and lexically indexed, including the context prefix.
        text           {TEXT} NOT NULL,
        -- What is shown to a reader, without the prefix.
        display_text   {TEXT} DEFAULT '',
        page           {INT}  DEFAULT 0,
        section        {TEXT} DEFAULT 'unknown',
        section_path   {JSON},
        label          {TEXT} DEFAULT '',
        caption        {TEXT} DEFAULT '',
        element_ids    {JSON},
        token_estimate {INT}  DEFAULT 0,
        extra          {JSON}
    )
    """,
    # ------------------------------------------------------------ conversation
    """
    CREATE TABLE IF NOT EXISTS threads (
        id            {PK},
        collection_id {INT},
        paper_id      {INT},
        title         {TEXT} DEFAULT '',
        -- The paper ids this thread is scoped to. A thread over one paper has
        -- one, a cross paper thread has several, and an empty list means the
        -- whole collection.
        paper_ids     {JSON},
        created_at    {TS}   NOT NULL,
        updated_at    {TS}   NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id          {PK},
        thread_id   {INT}  NOT NULL,
        role        {TEXT} NOT NULL,
        content     {TEXT} DEFAULT '',
        -- The chunks this answer was built from, kept so a citation still
        -- resolves after the paper has been re-indexed and chunk ids moved.
        citations   {JSON},
        -- Which lens produced this, empty for a plain question.
        lens        {TEXT} DEFAULT '',
        provider    {TEXT} DEFAULT '',
        model       {TEXT} DEFAULT '',
        usage       {JSON},
        retrieval   {JSON},
        created_at  {TS}   NOT NULL
    )
    """,
    # --------------------------------------------------------------- analyses
    """
    CREATE TABLE IF NOT EXISTS analyses (
        id          {PK},
        paper_id    {INT}  NOT NULL,
        lens        {TEXT} NOT NULL,
        content     {TEXT} DEFAULT '',
        citations   {JSON},
        provider    {TEXT} DEFAULT '',
        model       {TEXT} DEFAULT '',
        usage       {JSON},
        created_at  {TS}   NOT NULL
    )
    """,
    # ------------------------------------------------------------------- notes
    """
    CREATE TABLE IF NOT EXISTS notes (
        id          {PK},
        paper_id    {INT}  NOT NULL,
        -- The element this note is about. Null means the note is about the
        -- paper as a whole, which is what a general observation needs.
        element_id  {INT},
        body        {TEXT} NOT NULL,
        -- A colour rather than a tag vocabulary: deciding what the categories
        -- are is work, and picking a colour is not.
        colour      {TEXT} DEFAULT 'yellow',
        created_at  {TS}   NOT NULL,
        updated_at  {TS}   NOT NULL
    )
    """,
    # ------------------------------------------------------------------ usage
    """
    CREATE TABLE IF NOT EXISTS usage_log (
        id            {PK},
        paper_id      {INT},
        provider      {TEXT} DEFAULT '',
        model         {TEXT} DEFAULT '',
        kind          {TEXT} DEFAULT 'llm',
        operation     {TEXT} DEFAULT '',
        input_tokens  {INT}  DEFAULT 0,
        output_tokens {INT}  DEFAULT 0,
        units         {INT}  DEFAULT 0,
        cost_usd      {REAL} DEFAULT 0,
        created_at    {TS}   NOT NULL
    )
    """,
)

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does
# nothing to a table that already exists, so a database created before a column
# was added never gets it and every query naming it fails. These are applied
# with ALTER on every boot, and a column that is already there is skipped.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("papers", "read_state", "{TEXT} DEFAULT 'unread'"),
    ("papers", "verdict", "{TEXT} DEFAULT ''"),
    ("papers", "source_url", "{TEXT} DEFAULT ''"),
    ("papers", "quality", "{JSON}"),
)

INDEXES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_hash ON papers (file_hash)",
    "CREATE INDEX IF NOT EXISTS idx_papers_collection ON papers (collection_id)",
    "CREATE INDEX IF NOT EXISTS idx_papers_status ON papers (status)",
    "CREATE INDEX IF NOT EXISTS idx_papers_read_state ON papers (read_state)",
    # The elements view lists by paper in reading order, which is the single
    # most common query in the application.
    "CREATE INDEX IF NOT EXISTS idx_elements_paper_ord ON elements (paper_id, ord)",
    # and filters by kind for the figures and tables galleries, and by page for
    # the reader overlay.
    "CREATE INDEX IF NOT EXISTS idx_elements_kind ON elements (paper_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_elements_page ON elements (paper_id, page)",
    "CREATE INDEX IF NOT EXISTS idx_elements_section ON elements (paper_id, section)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_paper_ord ON chunks (paper_id, ord)",
    "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_threads_collection ON threads (collection_id, updated_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_analyses_lens ON analyses (paper_id, lens)",
    "CREATE INDEX IF NOT EXISTS idx_usage_paper ON usage_log (paper_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes (paper_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notes_element ON notes (element_id)",
)


def _columns(conn, dialect: str) -> dict[str, set[str]]:
    """Which columns each table already has, in whichever dialect this is."""
    out: dict[str, set[str]] = {}
    try:
        if dialect == "postgres":
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema()"
                )
                for row in cur.fetchall():
                    # The connection is opened with psycopg's dict_row factory,
                    # so a row is a mapping. Unpacking it as a pair yields its
                    # two key names instead of the values, which reads as a
                    # catalogue where every table is called "table_name" and
                    # sends every ALTER below straight into a table that already
                    # has the column.
                    if isinstance(row, dict):
                        table, column = row["table_name"], row["column_name"]
                    else:
                        table, column = row[0], row[1]
                    out.setdefault(str(table), set()).add(str(column))
        else:
            names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            ]
            for table in names:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                out[table] = {str(row[1]) for row in rows}
    except Exception:  # noqa: BLE001 - an unreadable catalogue means try the ALTER
        return {}
    return out


def _try(conn, dialect: str, sql: str) -> bool:
    """Run a statement that is allowed to fail, without losing the schema with it.

    Postgres aborts the entire transaction on any error, so a swallowed failure
    is not the local no-op it looks like: every later statement fails too and
    the commit throws away the tables created before it. The statements below
    are all ones that are *expected* to fail once they have been applied, which
    on a second boot turned the whole of create_all into a no-op and left a
    fresh database with no tables at all.

    A savepoint scopes the failure to the one statement. SQLite runs in
    autocommit here, where a failure is already local.
    """
    try:
        if dialect == "postgres":
            with conn.transaction():
                conn.execute(sql)
        else:
            conn.execute(sql)
        return True
    except Exception:  # noqa: BLE001
        return False


def create_all(db) -> None:
    # One connection for the whole schema, and `conn.execute` rather than
    # `db.execute`, because the latter calls ensure_schema and would recurse
    # straight back into here.
    with db.connect() as conn:
        for statement in TABLES:
            conn.execute(render_ddl(statement.strip(), db.dialect))

        existing = _columns(conn, db.dialect)
        for table, column, spec in ADDED_COLUMNS:
            if column in existing.get(table, set()):
                continue
            # A race with another process adding the same column, or a
            # catalogue that could not be read. Neither is worth refusing to
            # boot over, and neither may take the schema down with it.
            _try(conn, db.dialect,
                 f"ALTER TABLE {table} ADD COLUMN {column} "
                 + render_ddl(spec, db.dialect))

        for statement in INDEXES:
            # An index failing is never a reason not to boot. The usual cause is
            # a unique index over data that predates it, which is a problem to
            # report rather than one to crash on.
            _try(conn, db.dialect, statement)
