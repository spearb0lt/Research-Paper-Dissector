"""Reading text that lives inside an image rather than in the text layer.

Two very different jobs share one module because they share an engine.

**Scanned pages.** A page whose text layer is empty is a photograph of a page,
and nothing but OCR will read it. Without this such a paper indexes as nothing
at all and silently returns no results for every query.

**Figures.** A chart's axis labels, legend and data labels are frequently the
only place a number appears anywhere in the paper. "What was the accuracy at
epoch 50" is answerable from the figure and from nowhere else, and a pipeline
that indexes only the caption cannot answer it. This is the single highest
value use of OCR in a paper reader and the one most systems skip.

Three engines are supported, preferred in this order:

* RapidOCR, which runs on onnxruntime and needs no system binary and no torch.
  It is the only one that could in principle run on a slim deployment.
* EasyOCR, which is accurate and pulls in the whole torch stack.
* Tesseract, which needs a system binary installed separately. Listed last
  because "pytesseract is installed" and "Tesseract works" are different
  statements, and the gap between them is a confusing failure.

OCR is off by default. It is slow, it is wrong often enough on a dense figure
to be worth a user's decision, and on a paper with a good text layer it adds
nothing.
"""
from __future__ import annotations

import io
from typing import Any

from .. import blobs, settings
from ..runtime import current as runtime
from .base import Element, Kind, ParseResult

# Below this many characters an OCR result is noise from a line drawing rather
# than a label anyone typed, and indexing it only adds false matches.
_MIN_USEFUL_CHARS = 3


def engines_available() -> list[str]:
    from importlib.util import find_spec
    import shutil

    found: list[str] = []
    for name, module in (("rapidocr", "rapidocr_onnxruntime"), ("easyocr", "easyocr")):
        try:
            if find_spec(module) is not None:
                found.append(name)
        except (ImportError, ValueError):
            continue
    try:
        if find_spec("pytesseract") is not None and shutil.which("tesseract"):
            found.append("tesseract")
    except (ImportError, ValueError):
        pass
    return found


def _pick(preferred: str = "") -> str:
    available = engines_available()
    if not available:
        return ""
    wanted = (preferred or settings.OCR_ENGINE or "auto").strip().lower()
    if wanted and wanted != "auto":
        return wanted if wanted in available else ""
    return available[0]


class _Reader:
    """One loaded engine, reused across every image in a paper.

    Loading EasyOCR's detector and recogniser takes several seconds, so doing
    it per figure would make OCR on a twenty figure paper unusable.
    """

    def __init__(self, engine: str) -> None:
        self.engine = engine
        self._handle: Any = None

    def _load(self) -> Any:
        if self._handle is not None:
            return self._handle
        if self.engine == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR

            self._handle = RapidOCR()
        elif self.engine == "easyocr":
            import easyocr

            self._handle = easyocr.Reader(
                list(settings.OCR_LANGUAGES) or ["en"], gpu=False, verbose=False
            )
        elif self.engine == "tesseract":
            import pytesseract

            self._handle = pytesseract
        else:
            raise ValueError(f"Unknown OCR engine {self.engine!r}.")
        return self._handle

    def read(self, data: bytes) -> str:
        handle = self._load()
        if self.engine == "rapidocr":
            import numpy as np
            from PIL import Image as PILImage

            picture = PILImage.open(io.BytesIO(data)).convert("RGB")
            found, _elapsed = handle(np.asarray(picture))
            return " ".join(str(line[1]) for line in (found or []) if len(line) > 1)
        if self.engine == "easyocr":
            return " ".join(handle.readtext(data, detail=0, paragraph=True) or [])
        from PIL import Image as PILImage

        picture = PILImage.open(io.BytesIO(data))
        return handle.image_to_string(picture, lang="+".join(settings.OCR_LANGUAGES or ["en"]))


def run_ocr(result: ParseResult, pdf_path: str, *, log=None) -> int:
    """Fill in `ocr_text` on figures, and add text elements for scanned pages.

    Returns how many images were read. Mutates `result` in place, because the
    caller commits it as a whole and a second parse object would have to be
    merged back anyway.
    """
    def say(message: str) -> None:
        if log:
            log(message)

    if not runtime().can("ocr"):
        result.warnings.append(f"OCR is unavailable: {runtime().reason('ocr')}")
        return 0

    engine = _pick()
    if not engine:
        result.warnings.append(
            "No OCR engine is installed. Run: pip install rapidocr-onnxruntime"
        )
        return 0

    reader = _Reader(engine)
    say(f"Using {engine}")
    read = 0

    figures = [e for e in result.elements if e.kind is Kind.FIGURE and e.image]
    for position, element in enumerate(figures, start=1):
        data = blobs.get(element.image.digest, element.image.media_type)
        if not data:
            continue
        try:
            text = " ".join((reader.read(data) or "").split())
        except Exception as exc:  # noqa: BLE001 - one unreadable figure is survivable
            result.warnings.append(f"OCR failed on a figure on page {element.page}: {exc}")
            continue
        if len(text) >= _MIN_USEFUL_CHARS:
            element.image.ocr_text = text
            read += 1
        if position % 5 == 0:
            say(f"Read {position} of {len(figures)} figures")

    scanned = [page for page in result.pages if page.needs_ocr]
    if scanned:
        read += _ocr_pages(result, pdf_path, scanned, reader, say)

    say(f"OCR read {read} image(s)")
    return read


def _ocr_pages(result: ParseResult, pdf_path: str, pages, reader: _Reader, say) -> int:
    """Render each text-free page and read it, adding the text as elements."""
    from .fast import _fitz
    from .base import BBox
    from .sections import section_for_path

    try:
        book = _fitz().open(pdf_path)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"Scanned pages could not be rendered: {exc}")
        return 0

    read = 0
    try:
        order = max((e.order for e in result.elements), default=0)
        for info in pages:
            try:
                page = book[info.number - 1]
                matrix = _fitz().Matrix(settings.PAGE_RENDER_SCALE, settings.PAGE_RENDER_SCALE)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                data = pixmap.tobytes("png")
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"Page {info.number} could not be rendered: {exc}")
                continue

            info.render_digest = blobs.put(data, "image/png")
            try:
                text = " ".join((reader.read(data) or "").split())
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"OCR failed on page {info.number}: {exc}")
                continue
            if len(text) < 20:
                continue

            order += 1
            read += 1
            result.elements.append(Element(
                kind=Kind.PARAGRAPH,
                text=text,
                page=info.number,
                bbox=BBox(0.0, 0.0, info.width, info.height),
                order=order,
                section=section_for_path([]),
                extra={"source": "ocr", "engine": reader.engine},
            ))
            say(f"Read scanned page {info.number}")
    finally:
        book.close()

    if read:
        # Reading order is rebuilt because the OCR elements were appended at the
        # end rather than inserted at their page position.
        result.elements.sort(key=lambda e: (e.page, e.bbox.y0 if e.bbox else 0.0))
        for position, element in enumerate(result.elements):
            element.order = position
    return read
