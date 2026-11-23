"""Backends: canvas pages -> digital PDF (fpdf2, real text layer) or raster images (Pillow).

Scanned-style output rasterises the pages, then applies seeded rotation, blur, noise and
contrast loss. Every random choice comes from the ``random.Random`` passed in, and PDF dates
are fixed, so the same seed gives byte-identical files.
"""

from __future__ import annotations

import io
import random
from datetime import UTC, datetime

import segno
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from evals.synth.layouts import PAGE_H, PAGE_W, PT_MM, Canvas

FIXED_DATE = datetime(2026, 1, 1, tzinfo=UTC)
DPI = 150


def _qr_matrix(data: str) -> list[list[int]]:
    qr = segno.make(data, error="m", micro=False)
    return [list(row) for row in qr.matrix_iter(scale=1, border=0)]


# ---------- PDF ----------


def to_pdf(cv: Canvas) -> bytes:
    pdf = FPDF(unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_creation_date(FIXED_DATE)
    pdf.set_creator("invoice-flow synthetic generator")
    pdf.set_title("Synthetic invoice")
    for page in cv.pages:
        pdf.add_page()
        for op in page:
            _pdf_op(pdf, op)
    return bytes(pdf.output())


def _pdf_op(pdf: FPDF, op: tuple) -> None:
    kind = op[0]
    if kind == "text":
        _, x, y, s, size, bold, align, color = op
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.set_text_color(color)
        w = pdf.get_string_width(s)
        if align == "R":
            x -= w
        elif align == "C":
            x -= w / 2
        pdf.text(x, y, s)
    elif kind == "line":
        _, x1, y1, x2, y2, width = op
        pdf.set_draw_color(0)
        pdf.set_line_width(width)
        pdf.line(x1, y1, x2, y2)
    elif kind == "rect":
        _, x, y, w, h, fill, stroke = op
        pdf.set_line_width(0.2)
        pdf.set_draw_color(0)
        if fill is not None:
            pdf.set_fill_color(fill)
        style = ("F" if fill is not None else "") + ("D" if stroke else "")
        if style:
            pdf.rect(x, y, w, h, style="DF" if style == "FD" else style)
    elif kind == "qr":
        _, x, y, size, data = op
        m = _qr_matrix(data)
        cell = size / len(m)
        pdf.set_fill_color(0)
        for r, row in enumerate(m):
            for c, dark in enumerate(row):
                if dark:
                    pdf.rect(x + c * cell, y + r * cell, cell, cell, style="F")


# ---------- raster ----------

_FONTS: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font(px: int):
    key = (px, False)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.load_default(size=px)
    return _FONTS[key]


def to_images(cv: Canvas, dpi: int = DPI) -> list[Image.Image]:
    k = dpi / 25.4
    out = []
    for page in cv.pages:
        img = Image.new("L", (round(PAGE_W * k), round(PAGE_H * k)), 255)
        d = ImageDraw.Draw(img)
        for op in page:
            _raster_op(img, d, op, k)
        out.append(img)
    return out


def _raster_op(img: Image.Image, d: ImageDraw.ImageDraw, op: tuple, k: float) -> None:
    kind = op[0]
    if kind == "text":
        _, x, y, s, size, bold, align, color = op
        font = _font(max(6, round(size * PT_MM * k)))
        anchor = {"L": "ls", "R": "rs", "C": "ms"}[align]
        d.text(
            (x * k, y * k),
            s,
            fill=color,
            font=font,
            anchor=anchor,
            stroke_width=1 if bold and size >= 8 else 0,
            stroke_fill=color,
        )
    elif kind == "line":
        _, x1, y1, x2, y2, width = op
        d.line([(x1 * k, y1 * k), (x2 * k, y2 * k)], fill=0, width=max(1, round(width * k)))
    elif kind == "rect":
        _, x, y, w, h, fill, stroke = op
        d.rectangle(
            [x * k, y * k, (x + w) * k, (y + h) * k],
            fill=fill,
            outline=0 if stroke else None,
            width=1,
        )
    elif kind == "qr":
        _, x, y, size, data = op
        m = _qr_matrix(data)
        cell = size / len(m)
        for r, row in enumerate(m):
            for c, dark in enumerate(row):
                if dark:
                    x0, y0 = (x + c * cell) * k, (y + r * cell) * k
                    d.rectangle([x0, y0, x0 + cell * k, y0 + cell * k], fill=0)


def scan_params(rng: random.Random) -> dict:
    return {
        "dpi": DPI,
        "rotation_deg": round(rng.choice([-1, 1]) * rng.uniform(0.6, 3.0), 2),
        "blur_radius": round(rng.uniform(0.5, 1.2), 2),
        "noise": round(rng.uniform(0.06, 0.16), 3),
        "contrast": round(rng.uniform(0.75, 0.92), 2),
        "jpeg_quality": rng.randrange(55, 86),
    }


def scan_effect(img: Image.Image, p: dict, rng: random.Random) -> Image.Image:
    """Scanner look: slight skew, blur, sensor noise, grey paper, speckles."""
    img = img.rotate(p["rotation_deg"], resample=Image.Resampling.BICUBIC, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(p["blur_radius"]))
    noise = Image.frombytes("L", img.size, rng.randbytes(img.size[0] * img.size[1]))
    img = Image.blend(img, noise, p["noise"])
    img = ImageEnhance.Contrast(img).enhance(p["contrast"])
    d = ImageDraw.Draw(img)
    for _ in range(rng.randrange(40, 120)):  # dust specks
        x, y = rng.randrange(img.size[0]), rng.randrange(img.size[1])
        r = rng.choice([1, 1, 2])
        d.ellipse([x, y, x + r, y + r], fill=rng.randrange(40, 120))
    return img


def to_scanned(cv: Canvas, fmt: str, p: dict, rng: random.Random) -> bytes:
    """Rasterise and age the pages, then encode as ``png``, ``jpeg`` or ``pdf_scanned``."""
    pages = [scan_effect(img, p, rng) for img in to_images(cv, p["dpi"])]
    buf = io.BytesIO()
    if fmt == "png":
        if len(pages) != 1:
            raise ValueError("image invoices must be one page")
        pages[0].save(buf, format="PNG", optimize=False)
    elif fmt == "jpeg":
        if len(pages) != 1:
            raise ValueError("image invoices must be one page")
        pages[0].save(buf, format="JPEG", quality=p["jpeg_quality"])
    elif fmt == "pdf_scanned":
        # JPEG-compress each page first, as a scanner would, then wrap them in a PDF.
        jpgs = []
        for pg in pages:
            jb = io.BytesIO()
            pg.save(jb, format="JPEG", quality=p["jpeg_quality"])
            jpgs.append(Image.open(io.BytesIO(jb.getvalue())))
        jpgs[0].save(
            buf,
            format="PDF",
            save_all=True,
            append_images=jpgs[1:],
            resolution=p["dpi"],
            creationDate=FIXED_DATE.timetuple(),
            modDate=FIXED_DATE.timetuple(),
            producer="invoice-flow synthetic scanner",
            title="Scanned document",
        )
    else:
        raise ValueError(f"unknown scanned format {fmt!r}")
    return buf.getvalue()
