"""Checks on an incoming invoice file (FR-10, data-protection §4).

Allowed: PDF, PNG, JPEG, TIFF and WEBP, decided by the file's first bytes (magic bytes), never
by its name or the browser's content type. At most 20 MB and 50 pages. Encrypted PDFs,
archives and anything else are rejected, each with a reason a clerk can act on.

The file is untrusted. It is parsed only to count pages (pypdf, Pillow) and, for auto-assignment,
to read the PDF text layer for GSTINs that are matched against the entity registrations; its text
never drives anything else.
"""

from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import dataclass, field
from enum import StrEnum
from typing import BinaryIO

from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PyPdfError

CHUNK = 1024 * 1024
# A GSTIN, written plain or grouped for reading (`27 AAPFU0939F 1Z V`).
GSTIN_PATTERN = re.compile(
    r"(?<![0-9A-Z])([0-9]{2}) ?([A-Z]{5}[0-9]{4}[A-Z]) ?([0-9A-Z]Z) ?([0-9A-Z])(?![0-9A-Z])"
)
GSTIN_SCAN_PAGES = 2  # the buyer block is on the first page; allow one more


class FileType(StrEnum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    WEBP = "webp"


EXTENSIONS = {
    FileType.PDF: ".pdf",
    FileType.PNG: ".png",
    FileType.JPEG: ".jpg",
    FileType.TIFF: ".tif",
    FileType.WEBP: ".webp",
}
PILLOW_FORMATS = {
    FileType.PNG: "PNG",
    FileType.JPEG: "JPEG",
    FileType.TIFF: "TIFF",
    FileType.WEBP: "WEBP",
}

ALLOWED_TEXT = "PDF, PNG, JPEG, TIFF or WEBP"


class Rejected(Exception):
    """The file cannot be taken in. `reason` is shown to the clerk next to the file name."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CheckedFile:
    file_type: FileType
    size: int
    sha256: str
    page_count: int
    gstins: frozenset[str] = field(default_factory=frozenset)  # from the PDF text layer


def sniff(head: bytes) -> FileType | None:
    """The file type from its first bytes, or None if it is not an allowed type."""
    if head.startswith(b"%PDF-"):
        return FileType.PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileType.PNG
    if head.startswith(b"\xff\xd8\xff"):
        return FileType.JPEG
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return FileType.TIFF
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return FileType.WEBP
    return None


def _megabytes(n: int) -> str:
    if n < 1024 * 1024:
        return f"{max(1, n // 1024)} KB"
    return f"{n // (1024 * 1024)} MB"


def check_file(stream: BinaryIO, *, max_bytes: int, max_pages: int) -> CheckedFile:
    """Validate an uploaded file and hash it. Raises `Rejected` with the reason.

    `stream` must be seekable (an upload's spooled temporary file is).
    """
    stream.seek(0)
    head = stream.read(16)
    if not head:
        raise Rejected("The file is empty.")
    file_type = sniff(head)
    if file_type is None:
        raise Rejected(f"Not an allowed file type. Upload a {ALLOWED_TEXT} file.")

    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise Rejected(
                f"The file is larger than {_megabytes(max_bytes)}. Split or compress it."
            )
        digest.update(chunk)

    stream.seek(0)
    if file_type is FileType.PDF:
        pages, gstins = _check_pdf(stream)
    else:
        pages, gstins = _check_image(stream, file_type), frozenset()
    if pages > max_pages:
        raise Rejected(f"The file has {pages} pages; the limit is {max_pages}. Split it.")
    stream.seek(0)
    return CheckedFile(file_type, size, digest.hexdigest(), pages, gstins)


def _check_pdf(stream: BinaryIO) -> tuple[int, frozenset[str]]:
    try:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise Rejected("The PDF is encrypted or password-protected. Upload an unlocked copy.")
        pages = len(reader.pages)
    except Rejected:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError, OSError):
        raise Rejected("The PDF is damaged and cannot be read.") from None
    if pages == 0:
        raise Rejected("The PDF has no pages.")
    return pages, _pdf_gstins(reader)


def _pdf_gstins(reader: PdfReader) -> frozenset[str]:
    """GSTIN-shaped strings in the first pages' text layer (empty for scans)."""
    found: set[str] = set()
    for page in reader.pages[:GSTIN_SCAN_PAGES]:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: S112 - a bad text layer only disables auto-assignment
            continue
        found.update("".join(m) for m in GSTIN_PATTERN.findall(text.upper()))
    return frozenset(found)


def _check_image(stream: BinaryIO, file_type: FileType) -> int:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(stream) as image:
                if image.format != PILLOW_FORMATS[file_type]:
                    raise Rejected("The image is damaged and cannot be read.")
                frames = int(getattr(image, "n_frames", 1))
                image.verify()
                return frames
    except Rejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Rejected("The image is too large to process.") from None
    except Exception:  # Pillow raises many types for damaged files
        raise Rejected("The image is damaged and cannot be read.") from None
