"""The FastAPI application.

Three pieces of middleware carry most of this app's character.

The first binds any provider keys the request brought to a context variable for
exactly the lifetime of that request. That is what lets this be deployed
publicly with no keys of its own: a visitor's key rides their own requests and
is never stored, logged or echoed back.

The second turns the project's own exception types into a uniform error body
with a `hint`. Almost every failure here is something the user can act on, a
missing key, an unparseable PDF, a capability this platform does not have, so
the fix travels with the error rather than being buried in a log.

The third gates the whole API behind a password when one is set, for a public
deployment whose owner does not want strangers filling their disk.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import blobs, settings
from .api.extras import router as extras_router
from .api.routes import router
from .db import repo
from .embeddings import EmbeddingError
from .llm import LLMError, keyring
from .parse.base import ParseError
from .runtime import CapabilityError
from .runtime import current as runtime

logger = logging.getLogger("dissect")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # A serverless shutdown is capped at 500 milliseconds, so nothing slow may
    # run after the yield.
    try:
        repo.get_db().ensure_schema()
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        blobs.root().mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 - a broken database must still produce a
        # readable answer from /api/health rather than failing to boot.
        logger.error("Start up initialisation failed.", exc_info=True)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bind_request_credentials(request: Request, call_next):
    """Attach this request's provider keys, and detach them afterwards.

    The adapters are process wide singletons, so the credential cannot live on
    the adapter. A context variable is exactly the right scope: the object is
    shared between concurrent requests and the key is not.
    """
    keys: dict[str, str] = {}
    base_urls: dict[str, str] = {}
    accounts: dict[str, str] = {}

    if settings.ALLOW_CLIENT_KEYS:
        for name, value in request.headers.items():
            lowered = name.lower()
            if lowered.startswith(keyring.KEY_PREFIX):
                slug = lowered[len(keyring.KEY_PREFIX):]
                if cleaned := keyring.clean_key(value):
                    keys[slug] = cleaned
            elif lowered.startswith(keyring.BASE_URL_PREFIX):
                slug = lowered[len(keyring.BASE_URL_PREFIX):]
                if cleaned := keyring.clean_base_url(value):
                    base_urls[slug] = cleaned
            elif lowered.startswith(keyring.ACCOUNT_PREFIX):
                slug = lowered[len(keyring.ACCOUNT_PREFIX):]
                if cleaned := keyring.clean_account(value):
                    accounts[slug] = cleaned

    keyring.bind(keys, base_urls, accounts)
    try:
        return await call_next(request)
    finally:
        keyring.reset()


@app.middleware("http")
async def require_password(request: Request, call_next):
    if not settings.APP_PASSWORD:
        return await call_next(request)
    # Health has to answer without the password, or a platform health check
    # marks a perfectly working deployment as down.
    if request.url.path in ("/api/health", "/api/docs", "/api/openapi.json"):
        return await call_next(request)
    supplied = request.headers.get("x-app-password", "")
    if supplied != settings.APP_PASSWORD:
        return JSONResponse(
            status_code=401,
            content={"error": "This instance is password protected.",
                     "hint": "Enter the password in Settings."},
        )
    return await call_next(request)


def _error(status: int, message: str, hint: str = "", extra: dict[str, Any] | None = None):
    body: dict[str, Any] = {"error": message}
    if hint:
        body["hint"] = hint
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(ParseError)
async def parse_error(_: Request, exc: ParseError):
    return _error(422, exc.message, exc.hint, {"parser": exc.parser})


@app.exception_handler(LLMError)
async def llm_error(_: Request, exc: LLMError):
    # 502 rather than 500: the failure is upstream, and a retryable one is
    # worth the client knowing about.
    return _error(502, exc.message, exc.hint,
                  {"provider": exc.provider, "retryable": exc.retryable})


@app.exception_handler(EmbeddingError)
async def embedding_error(_: Request, exc: EmbeddingError):
    return _error(502, str(exc), getattr(exc, "hint", ""))


@app.exception_handler(CapabilityError)
async def capability_error(_: Request, exc: CapabilityError):
    return _error(501, str(exc), exc.reason, {"capability": exc.capability_id})


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content={
            "error": detail.get("message", "Request failed."),
            "hint": detail.get("hint", ""),
            **{k: v for k, v in detail.items() if k not in ("message", "hint")},
        })
    return _error(exc.status_code, str(detail))


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    logger.exception("Unhandled error")
    return _error(
        500,
        str(exc)[:400] if settings.DEBUG else "Something went wrong on the server.",
        "Check the server log for the full traceback." if not settings.DEBUG else "",
    )


app.include_router(router, prefix="/api")
app.include_router(extras_router, prefix="/api")


@app.get("/api")
def root() -> dict[str, Any]:
    return {
        "app": settings.APP_NAME,
        "tagline": settings.APP_TAGLINE,
        "tier": runtime().tier.value,
        "docs": "/api/docs",
    }
