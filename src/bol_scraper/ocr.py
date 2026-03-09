from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
import pytesseract
from PIL import Image

from bol_scraper.pdf_render import extract_embedded_text_by_page, render_pdf_to_images
from bol_scraper.vision_preprocess import preprocess_for_ocr


def _configure_tesseract() -> None:
    """
    On Windows, users may set TESSERACT_CMD in .env.
    If not set, pytesseract will rely on PATH.
    """
    import os

    cmd = os.getenv("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
        return

    # Common Windows install locations (UB Mannheim installer)
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            pytesseract.pytesseract.tesseract_cmd = c
            return


def _ocr_image_to_text(binary_255: np.ndarray) -> tuple[str, float]:
    """
    Returns (text, avg_confidence_0_to_100).
    """
    data = pytesseract.image_to_data(
        binary_255,
        output_type=pytesseract.Output.DICT,
        config="--oem 3 --psm 6",
    )
    texts = []
    confs: list[float] = []
    n = len(data.get("text", []))
    for i in range(n):
        t = (data["text"][i] or "").strip()
        if not t:
            continue
        try:
            c = float(data["conf"][i])
        except Exception:  # noqa: BLE001
            c = -1.0
        if c >= 0:
            confs.append(c)
        texts.append(t)
    text = " ".join(texts).strip()
    avg_conf = float(sum(confs) / len(confs)) if confs else 0.0
    return text, avg_conf


def ocr_pdf_to_pages_text(
    pdf_path: Path,
    *,
    dpi: int,
    debug_dir: Optional[Path],
    keep_images: bool,
) -> tuple[list[str], dict[str, Any]]:
    """
    Produces a list of per-page text, preferring embedded PDF text when present,
    otherwise using OCR. Also returns metadata for auditing.
    """
    _configure_tesseract()

    embedded = extract_embedded_text_by_page(pdf_path)
    render_dir = None
    pre_dir = None
    txt_dir = None
    if debug_dir:
        base = debug_dir / Path(pdf_path).stem
        render_dir = base / "render"
        pre_dir = base / "preprocess"
        txt_dir = base / "text"
        txt_dir.mkdir(parents=True, exist_ok=True)

    images, page_count = render_pdf_to_images(pdf_path, dpi=dpi, out_dir=render_dir if keep_images else None)

    pages_out: list[str] = []
    embedded_used: list[int] = []
    ocr_used: list[int] = []
    ocr_conf_by_page: dict[int, float] = {}
    ocr_errors_by_page: dict[int, str] = {}

    for idx in range(page_count):
        embedded_text = (embedded[idx] or "").strip()
        if len(embedded_text) >= 40:
            pages_out.append(embedded_text)
            embedded_used.append(idx + 1)
            if txt_dir:
                (txt_dir / f"page_{idx+1:03d}.embedded.txt").write_text(embedded_text, encoding="utf-8")
            continue

        pil = images[idx]
        bgr = np.array(pil)[:, :, ::-1].copy()
        bin_img = preprocess_for_ocr(bgr, deskew=True, denoise=True, target_width=2200)

        if pre_dir and keep_images:
            pre_dir.mkdir(parents=True, exist_ok=True)
            Image.fromarray(bin_img).save(pre_dir / f"page_{idx+1:03d}.png")

        try:
            text, conf = _ocr_image_to_text(bin_img)
        except pytesseract.TesseractNotFoundError as e:
            text, conf = "", 0.0
            ocr_errors_by_page[idx + 1] = str(e)
        ocr_used.append(idx + 1)
        ocr_conf_by_page[idx + 1] = float(conf)
        page_text = text.strip()
        if txt_dir:
            (txt_dir / f"page_{idx+1:03d}.ocr.txt").write_text(
                f"CONF={conf:.1f}\n\n{page_text}\n",
                encoding="utf-8",
            )
        pages_out.append(page_text)

    meta = {
        "page_count": page_count,
        "embedded_text_used_pages": embedded_used,
        "ocr_used_pages": ocr_used,
        "ocr_avg_conf_by_page": ocr_conf_by_page,
        "ocr_errors_by_page": ocr_errors_by_page,
    }
    return pages_out, meta

