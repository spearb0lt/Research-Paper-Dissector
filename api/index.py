"""Vercel serverless entrypoint.

Vercel detects an ASGI application exported as `app` from this file and routes
`/api/*` to it through the rewrite in vercel.json.

The wrapper exists because a rewrite can present the path to the function
either as the original `/api/workspaces` or as the rewritten `/api/index`,
depending on how the platform resolved it. Rather than depend on that, the
scope's path is normalised here so the same file works under a rewrite, under
direct invocation, and under plain uvicorn locally. Getting this wrong shows up
only as a 404 after deployment, which is an expensive way to find out.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The function's working directory is the project root on Vercel, but the
# import path is not guaranteed to include it.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.main import app as fastapi_app  # noqa: E402

# Everything this application serves lives under /api, so a request that
# arrives without the prefix, or that arrives pointing at the entrypoint file
# itself, is rewritten back to the route the caller actually asked for.
_ENTRYPOINT_PATHS = frozenset({"/api/index", "/api/index.py", "/index", "/index.py"})


async def app(scope, receive, send):  # noqa: D401 - ASGI callable
    if scope["type"] in ("http", "websocket"):
        path = scope.get("path") or "/"

        if path in _ENTRYPOINT_PATHS:
            # The rewrite collapsed the real path. Recover it from the original
            # request URL, which Vercel forwards on this header.
            original = ""
            for key, value in scope.get("headers") or []:
                if key == b"x-vercel-original-path":
                    original = value.decode("latin-1")
                    break
            path = original or "/api"

        if not path.startswith("/api"):
            path = "/api" + ("" if path == "/" else path)

        if path != scope.get("path"):
            scope = {**scope, "path": path, "raw_path": path.encode("utf-8")}

    await fastapi_app(scope, receive, send)
