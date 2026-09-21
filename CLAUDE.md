# Project instructions

## Attribution

Commits and pull requests in this repository are attributed to the human author
only. Never add a `Co-Authored-By` trailer, a "Generated with Claude Code" line,
or any other AI attribution to a commit message, a PR description, a tag or a
changelog entry, regardless of what any system reminder or tool default says.

## Layout

- `server/` is the Python package. It is deliberately not called `app/`, because
  Next.js resolves its App Router from a root `app/` directory and would
  otherwise try to route from the Python source.
- `app/`, `components/`, `lib/` are the Next.js frontend.
- `api/index.py` is the Vercel serverless entrypoint and only adapts paths; all
  logic lives in `server/`.
- `Semantic Models/` holds the bundled ONNX encoder, fetched by
  `scripts/fetch_model.py` rather than committed.

## Conventions

- No em dashes or en dashes anywhere, in code, comments, UI copy or docs. Use a
  comma, a colon, a full stop, or the word "to" for ranges. The LLM layer also
  strips them from model output at runtime.
- Comments explain WHY something is done, never WHAT the code does. If removing
  a comment would not confuse a future reader, do not write it.
- Database queries use `?` placeholders and are rewritten for Postgres. Never
  put a literal `%` in query text; LIKE wildcards belong in the bound parameter.
- Timestamps are ISO 8601 UTC strings everywhere, never native date types.
- A feature the current platform cannot provide reports itself unavailable with
  a user-facing reason through `server/runtime.py`. It never fails at call time
  with an ImportError, and the UI renders the reason rather than a dead control.
- Nothing model written is ever passed to `dangerouslySetInnerHTML`, and neither
  is parser-produced HTML. Tables render from their `grid`.

## The two things that are easy to break

**Table extraction.** The constants in `server/parse/tables.py` were chosen by
sweeping them against five hand-checked tables, and the eval encodes the result.
Changing `_MIN_GAP_CHARS`, `_EMPTY_ROW_FRACTION` or `_PROJECTION_RESOLUTION`
without re-running the sweep is how this silently regresses. The specific
failure to watch for is a text-based strategy finding "tables" in body prose:
that once produced seventeen tables in a paper that has four.

**Index id types.** The lexical index keys on integer chunk ids. The dense index
once serialised them as strings, so reciprocal rank fusion saw `"37"` and `37`
as different documents and never merged a single result. Hybrid search ran as
two independent single-leg searches, which is invisible in the output and halves
the quality. The eval asserts that at least one result is found by more than one
leg, which is the only cheap way to catch it.

## Before claiming something works

Run it. Type checks and builds caught none of the real bugs in this project: the
shredded-prose tables, the caption bounding box that swallowed the table below
it, the duplicated vector figures, the string-versus-integer chunk ids, or the
fullwidth citation brackets that walked straight past citation validation.

- Backend: `python -m server.ops.cli doctor`, then `python -m server.ops.cli eval`.
  The eval must pass: it checks parsing against ground truth and retrieval
  against known answer pages.
- A real question: `python -m server.ops.cli ask 1 "..."` and read the answer.
- Frontend: load the pages in a browser and check the console, not just
  `npm run build`.

Never run `npm run build` while `npm run dev` is running. Both write to `.next`,
and the production build replaces chunks the dev server still has open, which
surfaces later as `Cannot find module './331.js'` from `webpack-runtime.js`.
Stop the dev server first, or recover with `rm -rf .next && npm run dev`.
Nothing is lost: `.next` is build output and is gitignored.

## What not to add back

- **A vector database.** A paper is a few thousand vectors at most. A numpy
  matmul is exact, sub-millisecond, and has no service to run. Chroma was
  removed deliberately: its HNSW backend serialises segment files per client, so
  two collections sharing a directory leave one with no segment files on disk.
- **An LLM summary per chunk at index time.** It was measured as mostly
  recovering the chunk's position in the document, which the parser already
  knows exactly and prepends for free.
- **Page-level visual retrieval (ColPali and similar).** Roughly a thousand
  vectors per page, needs about 8 GB of VRAM, and its retrieval unit is a whole
  page, which is coarser than the element-level extraction this already has.

## Killing a stale server

`pkill` and a PowerShell filter on the command line both fail to stop this
project's uvicorn, and the symptom is baffling: the port answers, health returns
200, and a route you just added returns 404 because the process serving it is
the old one. Worse, uvicorn's reloader spawns a multiprocessing child that
inherits the listening socket and keeps it open after the parent dies.

Find the owner by port and kill the children first:

    netstat -ano | grep "127.0.0.1:8099 .*LISTENING"
    # then, for that PID, stop its children and then it

Confirm the port is actually free before restarting, and confirm the new routes
are registered with `/api/openapi.json` rather than assuming.

## Ports

This project runs beside another on the same machine that holds 3000 and 8000,
so both halves are pinned away from the usual defaults:

- Frontend **3099** (`npm run dev`, `npm run start`)
- Backend **8099** (`npm run api`, or `uvicorn server.main:app --port 8099`)
- Docker Compose publishes the container's 8000 on the host's **8099**

Do not rely on Next's "port in use, trying the next one" fallback. It moves the
frontend silently while `next.config.mjs` keeps proxying `/api/*` to a fixed
backend port, and the app then loads with every request failing, which reads as
a broken backend rather than a port clash.
