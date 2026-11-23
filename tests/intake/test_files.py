"""FR-10 / data-protection §4: allowed types by magic bytes, 20 MB, 50 pages, no encrypted PDFs."""

import hashlib
import io

import pytest

from app.intake.files import FileType, Rejected, check_file, sniff
from tests.intake import samples

MB = 1024 * 1024


def check(data: bytes, *, max_bytes: int = 20 * MB, max_pages: int = 50):
    return check_file(io.BytesIO(data), max_bytes=max_bytes, max_pages=max_pages)


def reason(data: bytes, **limits) -> str:
    with pytest.raises(Rejected) as exc:
        check(data, **limits)
    return exc.value.reason


@pytest.mark.parametrize(
    "data,file_type,pages",
    [
        (samples.pdf(3), FileType.PDF, 3),
        (samples.image("PNG"), FileType.PNG, 1),
        (samples.image("JPEG"), FileType.JPEG, 1),
        (samples.image("TIFF", frames=4), FileType.TIFF, 4),
        (samples.image("WEBP"), FileType.WEBP, 1),
    ],
    ids=["pdf", "png", "jpeg", "tiff", "webp"],
)
def test_allowed_types_are_accepted_with_type_size_hash_and_pages(data, file_type, pages):
    checked = check(data)
    assert checked.file_type is file_type
    assert checked.size == len(data)
    assert checked.page_count == pages
    assert checked.sha256 == hashlib.sha256(data).hexdigest()


def test_type_is_decided_by_content_not_name():
    # A text file is rejected whatever it is called: the name never reaches the check.
    assert sniff(b"hello, this is not a pdf") is None
    assert "Not an allowed file type" in reason(b"Invoice no. 42\nTotal 1,000\n")
    # ...and a real PDF is accepted even without a .pdf name (the check never sees names).
    assert check(samples.pdf()).file_type is FileType.PDF


@pytest.mark.parametrize(
    "data",
    [
        samples.zip_archive(),
        b"GIF89a" + b"\x00" * 32,
        b"<html><script>alert(1)</script></html>",
        b"MZ\x90\x00" + b"\x00" * 60,  # Windows executable
    ],
    ids=["zip", "gif", "html", "exe"],
)
def test_other_types_and_archives_are_rejected(data):
    assert reason(data) == "Not an allowed file type. Upload a PDF, PNG, JPEG, TIFF or WEBP file."


def test_empty_file_is_rejected():
    assert reason(b"") == "The file is empty."


def test_file_over_the_size_limit_is_rejected():
    data = samples.pdf() + b"\n%" + b"x" * 2048
    assert check(data, max_bytes=len(data)).size == len(data)  # exactly at the limit: fine
    assert "larger than" in reason(data, max_bytes=len(data) - 1)


def test_default_limit_is_20_mb():
    from app.config import Settings

    assert Settings().max_upload_bytes == 20 * MB
    assert Settings().max_pages == 50
    big = b"%PDF-1.7\n" + b"0" * (20 * MB)
    assert reason(big) == "The file is larger than 20 MB. Split or compress it."


def test_pdf_over_50_pages_is_rejected_and_50_is_fine():
    assert check(samples.pdf(50)).page_count == 50
    assert reason(samples.pdf(51)) == "The file has 51 pages; the limit is 50. Split it."


def test_multi_page_tiff_counts_pages_against_the_limit():
    assert "the limit is 3" in reason(samples.image("TIFF", frames=4), max_pages=3)


def test_encrypted_pdf_is_rejected():
    assert reason(samples.pdf(password="secret")) == (
        "The PDF is encrypted or password-protected. Upload an unlocked copy."
    )


def test_damaged_files_are_rejected_with_a_reason():
    assert reason(b"%PDF-1.7\nthis is not really a pdf") == "The PDF is damaged and cannot be read."
    png_header_only = samples.image("PNG")[:24]
    assert reason(png_header_only) == "The image is damaged and cannot be read."


def test_gstins_are_read_from_the_pdf_text_layer_only():
    data = samples.text_pdf("Bill to: Entity Two Pvt Ltd", "GSTIN: 27ZXCPB0002B1Z5", "Total 100")
    assert check(data).gstins == frozenset({"27ZXCPB0002B1Z5"})
    grouped = samples.text_pdf("Buyer GSTIN 27 ZXCPA0001A 1Z 3 (Maharashtra)")
    assert check(grouped).gstins == frozenset({"27ZXCPA0001A1Z3"})
    assert check(samples.image("PNG")).gstins == frozenset()
