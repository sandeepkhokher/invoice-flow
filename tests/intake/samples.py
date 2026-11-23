"""Synthetic sample files for intake tests, built in memory (data-protection §7: no real ones)."""

from __future__ import annotations

import io
import zipfile

from fpdf import FPDF
from PIL import Image
from pypdf import PdfWriter


def pdf(pages: int = 1, *, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    if password:
        writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def text_pdf(*lines: str) -> bytes:
    """A digital PDF with a text layer (what auto-assignment reads)."""
    doc = FPDF()
    doc.add_page()
    doc.set_font("Helvetica", size=11)
    for line in lines:
        doc.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())


def image(fmt: str, *, frames: int = 1, colour: str = "white") -> bytes:
    out = io.BytesIO()
    first = Image.new("RGB", (40, 30), colour)
    if frames > 1:
        more = [Image.new("RGB", (40, 30), colour) for _ in range(frames - 1)]
        first.save(out, format=fmt, save_all=True, append_images=more)
    else:
        first.save(out, format=fmt)
    return out.getvalue()


def zip_archive() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("invoice.pdf", pdf())
    return out.getvalue()
