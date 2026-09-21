"""Getting a paper into the library from an identifier instead of a file.

Every paper you meet is a link or an arXiv id, and requiring the PDF on disk
first is the largest piece of friction in the whole tool. This accepts:

    2407.01449                          an arXiv id
    arXiv:2407.01449v2                  with the prefix and a version
    https://arxiv.org/abs/2407.01449    a landing page
    https://arxiv.org/pdf/2407.01449    a PDF link
    10.1109/cvpr.2016.90                a DOI
    https://doi.org/10.1109/...         a DOI link
    https://openreview.net/forum?id=..  a venue landing page
    https://any.host/paper.pdf          a direct PDF

This is server side fetching of a URL a user supplied, which is a request
forgery primitive if built carelessly: a URL pointing at 127.0.0.1, at a cloud
metadata endpoint, or at a private range would make the server read things the
user cannot reach themselves. So every host is resolved and checked against the
private ranges before connecting, and again on every redirect hop, because a
public host is free to redirect to a private one.

What comes back is also checked. A response is accepted only if it actually
starts with the PDF magic bytes, whatever the server claimed its content type
was, and only up to the configured upload limit.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from . import settings

# "2407.01449", "2407.01449v2", and the old style "cs/0112017".
_ARXIV_ID = re.compile(r"^(?:arxiv[:\s]*)?((?:[a-z-]+(?:\.[A-Z]{2})?/)?\d{4}\.\d{4,5}|[a-z-]+/\d{7})(v\d+)?$", re.IGNORECASE)
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/((?:[a-z-]+/)?\d{4}\.\d{4,5}|[a-z-]+/\d{7})", re.IGNORECASE)
_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:a-z0-9]+)$", re.IGNORECASE)

# Hosts that are never a paper and are usually a mistake or an attack.
_BLOCKED_HOSTS = frozenset({
    "localhost", "metadata.google.internal", "metadata.goog",
    "instance-data", "169.254.169.254",
})

_MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        return {"message": self.message, "hint": self.hint}


@dataclass
class Fetched:
    data: bytes
    filename: str
    source_url: str
    arxiv_id: str = ""
    doi: str = ""


def _is_public(host: str) -> bool:
    """Whether a hostname resolves only to addresses on the public internet."""
    if not host or host.lower() in _BLOCKED_HOSTS:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        # Every one of these is somewhere the server can reach and the user
        # cannot, which is the whole of the risk.
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
            or parsed.is_unspecified
        ):
            return False
    return True


def _check(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(
            f"Only http and https can be fetched, not {parsed.scheme or 'that'}.",
            hint="Paste a web link, an arXiv id or a DOI.",
        )
    if not _is_public(parsed.hostname or ""):
        raise FetchError(
            "That address is not on the public internet.",
            hint=(
                "Links to localhost, a private network or a cloud metadata "
                "endpoint are refused. Upload the file directly instead."
            ),
        )
    return url


def identify(text: str) -> dict[str, str]:
    """Work out what a pasted string is, and where its PDF would be."""
    value = (text or "").strip()
    if not value:
        raise FetchError("Nothing was pasted.")

    if match := _ARXIV_ID.match(value):
        arxiv_id = match.group(1) + (match.group(2) or "")
        return {"kind": "arxiv", "arxiv_id": arxiv_id,
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "view_url": f"https://arxiv.org/abs/{arxiv_id}"}

    if match := _ARXIV_URL.search(value):
        arxiv_id = match.group(1)
        return {"kind": "arxiv", "arxiv_id": arxiv_id,
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "view_url": f"https://arxiv.org/abs/{arxiv_id}"}

    if match := _DOI.match(value):
        return {"kind": "doi", "doi": match.group(1), "pdf_url": "",
                "view_url": f"https://doi.org/{match.group(1)}"}

    if value.lower().startswith(("http://", "https://")):
        return {"kind": "url", "pdf_url": value, "view_url": value}

    raise FetchError(
        "That is not an arXiv id, a DOI or a link.",
        hint="Try 2407.01449, 10.1109/cvpr.2016.90, or a link ending in .pdf.",
    )


def _download(url: str) -> tuple[bytes, str]:
    """Fetch a URL, re-checking the host on every redirect."""
    import requests

    current = _check(url)
    session = requests.Session()
    for _ in range(_MAX_REDIRECTS + 1):
        response = session.get(
            current,
            timeout=settings.FETCH_TIMEOUT,
            headers={"User-Agent": settings.USER_AGENT, "Accept": "application/pdf,*/*"},
            stream=True,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            target = response.headers.get("Location", "")
            response.close()
            if not target:
                raise FetchError("That link redirected to nowhere.")
            current = _check(requests.compat.urljoin(current, target))
            continue

        if response.status_code >= 400:
            response.close()
            raise FetchError(
                f"The server answered {response.status_code}.",
                hint=(
                    "The paper may be behind a paywall or need a login. Open it "
                    "in a browser and upload the PDF instead."
                ),
            )

        # Read with a cap rather than trusting Content-Length, which a server
        # may understate or omit entirely.
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > settings.MAX_UPLOAD_BYTES:
                response.close()
                raise FetchError(
                    f"That file is larger than the "
                    f"{settings.MAX_UPLOAD_BYTES // 1_000_000} MB limit."
                )
        response.close()
        return b"".join(chunks), current

    raise FetchError("That link redirected too many times.")


def fetch(text: str) -> Fetched:
    """Resolve an identifier and download the PDF behind it."""
    if not settings.FETCH_BY_URL:
        raise FetchError(
            "Fetching by link is switched off on this deployment.",
            hint="Set FETCH_BY_URL=1, or upload the file directly.",
        )

    found = identify(text)
    pdf_url = found.get("pdf_url") or ""

    if found["kind"] == "doi" and not pdf_url:
        from . import refs

        resolved = refs.from_crossref(found.get("doi", ""))
        # Crossref only lists a PDF link when the publisher registered a free
        # one, which is the honest signal for whether this can be fetched.
        pdf_url = (resolved.pdf_url if resolved else "") or ""
        if not pdf_url:
            raise FetchError(
                "That DOI has no freely downloadable PDF registered.",
                hint=(
                    f"Open {found.get('view_url', '')} to see whether you have "
                    "access, then upload the PDF."
                ),
            )

    data, final_url = _download(pdf_url)

    # The magic bytes, not the content type. A paywall or a login wall answers
    # 200 with an HTML page, and calling that a PDF produces a baffling parse
    # error several steps later instead of a clear message here.
    if not data.startswith(b"%PDF"):
        raise FetchError(
            "That link did not return a PDF.",
            hint=(
                "It is probably a landing page, a paywall or a login. Open it "
                "in a browser and upload the PDF from there."
            ),
        )

    return Fetched(
        data=data,
        filename=_filename(found, final_url),
        source_url=found.get("view_url") or final_url,
        arxiv_id=found.get("arxiv_id", ""),
        doi=found.get("doi", ""),
    )


def _filename(found: dict[str, str], url: str) -> str:
    if found.get("arxiv_id"):
        return f"arxiv-{found['arxiv_id'].replace('/', '-')}.pdf"
    if found.get("doi"):
        return f"doi-{found['doi'].replace('/', '-')}.pdf"
    tail = urlparse(url).path.rsplit("/", 1)[-1] or "paper.pdf"
    return tail if tail.lower().endswith(".pdf") else f"{tail}.pdf"
