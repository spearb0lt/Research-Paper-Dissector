"""Content addressed storage for PDFs, figure crops and page renders.

Binary data is kept out of the database on purpose. A paper with sixty figures
is tens of megabytes, and putting that in the elements table makes every query
that touches a paper pay for it. Addressing by SHA-256 also deduplicates for
free: the same figure appearing on two pages, or the same paper uploaded twice,
is stored once.

Which leaves the deployments that have no filesystem worth the name.

On a serverless tier every instance gets its own empty /tmp, so bytes written
while serving one request are invisible to the next and gone within minutes.
Measured on Vercel with a paper already uploaded: eight of twelve concurrent
requests saw it and four saw an empty library, and a few minutes later none of
them did. The database is the only storage those instances share, so when
`runtime.persistent_disk` is false the bytes are written there as well.

The filesystem is still the thing every read goes through. A row is fetched
once and written to the local path, so the second read of a page render is a
file read on that instance rather than a query, and `path_of` can hand a real
path to PyMuPDF, which needs one and cannot take a row. Where the disk does
persist, nothing is written to the database at all and this is the module it
always was.
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


def _in_database() -> bool:
    """Whether the database is holding the durable copy of every blob."""
    return not persistent()


def _repo():
    # Imported lazily because the database layer is heavier than this module and
    # a parse that never touches a blob should not pay to load it.
    from .db import repo

    return repo


def _path_for(digest: str, media_type: str) -> Path:
    # Two levels of hex fanout. A single flat directory with tens of thousands
    # of files is slow to list on every filesystem and unusable on some.
    suffix = MEDIA_TYPES.get(media_type, "")
    return root() / digest[:2] / digest[2:4] / f"{digest}{suffix}"


def _write_file(target: Path, data: bytes) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling and rename, so a reader never sees a partial file
        # if two requests store the same blob at once.
        staging = target.with_suffix(target.suffix + f".{os.getpid()}.part")
        staging.write_bytes(data)
        staging.replace(target)
        return True
    except OSError:
        # A full or read only /tmp. When the database holds the durable copy
        # this costs a query per read and nothing else, so it is not fatal.
        return False


def put(data: bytes, media_type: str = "application/octet-stream") -> str:
    """Store bytes and return their digest. Writing the same bytes twice is free."""
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"Refusing to store an unsupported media type: {media_type!r}")
    digest = hashlib.sha256(data).hexdigest()

    target = _path_for(digest, media_type)
    if not target.exists():
        _write_file(target, data)

    if _in_database():
        try:
            _repo().put_blob(digest, media_type, data)
        except Exception:  # noqa: BLE001 - the local copy still serves this request
            pass

    return digest


def get(digest: str, media_type: str = "image/png") -> bytes | None:
    path = _path_for(digest, media_type)
    try:
        return path.read_bytes()
    except OSError:
        pass

    if not _in_database():
        return None

    try:
        data = _repo().get_blob(digest)
    except Exception:  # noqa: BLE001
        return None
    if data is None:
        return None

    # Cached on the way out, so repeated reads of the same page render on this
    # instance cost one query rather than one per read.
    _write_file(path, data)
    return data


def path_of(digest: str, media_type: str = "image/png") -> Path | None:
    """A real filesystem path, for the callers that can only take one.

    PyMuPDF opens a path, not bytes, so on a database backed deployment the row
    is materialised into the local cache here and that path is returned.
    """
    path = _path_for(digest, media_type)
    if path.exists():
        return path
    if not _in_database():
        return None
    return path if get(digest, media_type) is not None and path.exists() else None


def exists(digest: str, media_type: str = "image/png") -> bool:
    if _path_for(digest, media_type).exists():
        return True
    if not _in_database():
        return False
    try:
        return _repo().blob_exists(digest)
    except Exception:  # noqa: BLE001
        return False


def delete(digest: str, media_type: str = "image/png") -> bool:
    removed = False
    try:
        _path_for(digest, media_type).unlink()
        removed = True
    except OSError:
        pass
    if _in_database():
        try:
            removed = _repo().delete_blob(digest) or removed
        except Exception:  # noqa: BLE001
            pass
    return removed


def usage_bytes() -> int:
    if _in_database():
        # The local files are a cache of these rows, so counting both would
        # report a store twice the size it is.
        try:
            return _repo().blob_usage_bytes()
        except Exception:  # noqa: BLE001
            pass
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
    if _in_database():
        try:
            _repo().clear_blobs()
        except Exception:  # noqa: BLE001
            pass


def collect_orphans() -> tuple[int, int]:
    """Delete stored bytes no paper refers to any more. Returns (count, bytes).

    Deleting a paper cannot delete its blobs directly, because storage is
    content addressed and therefore shared: the same figure can appear on two
    pages and the same PDF can be uploaded twice, so a digest is only garbage
    once nothing at all points at it.

    This matters more on a database backed deployment than it ever did on a
    disk. A paper is a few megabytes of PDF, a crop per figure and a cached
    render per page viewed, so a library that is added to and cleared out a few
    times will quietly outgrow a free Postgres tier while showing an empty
    shelf. The Remove button promises "everything extracted from it", and this
    is the half of that promise the row deletes do not keep.
    """
    repo = _repo()
    referenced = repo.referenced_digests()
    removed = 0
    freed = 0

    if _in_database():
        for digest, size in repo.stored_digests():
            if digest in referenced:
                continue
            if repo.delete_blob(digest):
                removed += 1
                freed += size

    # The local files too, whether they are the store or a cache of it. An
    # orphan left here is still holding a disk the operator is paying for.
    for path in root().rglob("*"):
        try:
            if not path.is_file() or path.name.endswith(".part"):
                continue
            if path.stem in referenced:
                continue
            size = path.stat().st_size
            path.unlink()
            if not _in_database():
                removed += 1
                freed += size
        except OSError:
            continue

    return removed, freed
