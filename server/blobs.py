"""Content addressed storage for PDFs, figure crops and page renders.

Binary data is kept out of the database on purpose. A paper with sixty figures
is tens of megabytes, and putting that in the elements table makes every query
that touches a paper pay for it. Addressing by SHA-256 also deduplicates for
free: the same figure appearing on two pages, or the same paper uploaded twice,
is stored once.

On a serverless deployment the writable directory is /tmp and is cleared when
the instance is recycled, so `available()` reports false for persistence and
the API tells the caller that uploads will not survive. Nothing here pretends
otherwise, because a figure that silently vanishes is worse than one that was
never offered.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from . import settings
from .runtime import current as runtime

# Media types this store will serve. An uploaded file that is not on this list
# is refused rather than served with a guessed type, because a served file with
# the wrong content type is how a document store becomes an XSS vector.
MEDIA_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def root() -> Path:
    return settings.BLOB_DIR


def persistent() -> bool:
    return runtime().can("persistent_disk")


def _path_for(digest: str, media_type: str) -> Path:
    # Two levels of hex fanout. A single flat directory with tens of thousands
    # of files is slow to list on every filesystem and unusable on some.
    suffix = MEDIA_TYPES.get(media_type, "")
    return root() / digest[:2] / digest[2:4] / f"{digest}{suffix}"


def put(data: bytes, media_type: str = "application/octet-stream") -> str:
    """Store bytes and return their digest. Writing the same bytes twice is free."""
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"Refusing to store an unsupported media type: {media_type!r}")
    digest = hashlib.sha256(data).hexdigest()
    target = _path_for(digest, media_type)
    if target.exists():
        return digest
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling and rename, so a reader never sees a partial file if
    # two requests store the same blob at once.
    staging = target.with_suffix(target.suffix + f".{os.getpid()}.part")
    staging.write_bytes(data)
    staging.replace(target)
    return digest


def get(digest: str, media_type: str = "image/png") -> bytes | None:
    path = _path_for(digest, media_type)
    try:
        return path.read_bytes()
    except OSError:
        return None


def path_of(digest: str, media_type: str = "image/png") -> Path | None:
    path = _path_for(digest, media_type)
    return path if path.exists() else None


def exists(digest: str, media_type: str = "image/png") -> bool:
    return _path_for(digest, media_type).exists()


def delete(digest: str, media_type: str = "image/png") -> bool:
    path = _path_for(digest, media_type)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def usage_bytes() -> int:
    total = 0
    for path in root().rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def clear() -> None:
    """Remove everything. Used by the reset command, never by a request."""
    shutil.rmtree(root(), ignore_errors=True)
