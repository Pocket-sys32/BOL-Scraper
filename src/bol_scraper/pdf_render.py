from __future__ import annotations

from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image


def render_pdf_to_images(
    pdf_path: Path,
    *,
    dpi: int,
    out_dir: Optional[Path] = None,
) -> tuple[list[Image.Image], int]:
    """
    Render each PDF page to a PIL Image at a given DPI.
    If out_dir is provided, raw rendered pages are written as PNGs.
    """
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    images: list[Image.Image] = []
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for i in range(page_count):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
        if out_dir:
            (out_dir / f"page_{i+1:03d}.png").write_bytes(pix.tobytes("png"))

    return images, page_count


def extract_embedded_text_by_page(pdf_path: Path) -> list[str]:
    doc = fitz.open(pdf_path)
    out: list[str] = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        out.append(page.get_text("text") or "")
    return out

