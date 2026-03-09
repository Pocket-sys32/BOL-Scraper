from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from bol_scraper.cache import Cache
from bol_scraper.extract_llm import extract_fields_with_llm
from bol_scraper.extract_rules import extract_fields_with_rules
from bol_scraper.google_maps import compute_route_miles
from bol_scraper.models import DocumentResult
from bol_scraper.ocr import ocr_pdf_to_pages_text

console = Console()


def run_pipeline(
    pdfs: list[Path],
    *,
    dpi: int,
    debug_dir: Optional[Path],
    keep_images: bool,
    cache_db: Path,
    skip_llm: bool,
) -> list[DocumentResult]:
    cache = Cache(cache_db)
    results: list[DocumentResult] = []

    for pdf_path in pdfs:
        console.print(f"[bold]Processing[/bold] {pdf_path}")
        doc = DocumentResult(source_path=str(pdf_path), page_count=0)

        try:
            pages_text, meta = ocr_pdf_to_pages_text(
                pdf_path,
                dpi=dpi,
                debug_dir=debug_dir,
                keep_images=keep_images,
            )
            doc.page_count = meta["page_count"]
            doc.embedded_text_used_pages = meta["embedded_text_used_pages"]
            doc.ocr_used_pages = meta["ocr_used_pages"]
            doc.ocr_avg_conf_by_page = meta.get("ocr_avg_conf_by_page", {})

            empty_pages = [i + 1 for i, t in enumerate(pages_text) if not (t or "").strip()]
            if empty_pages:
                doc.errors.append(f"Empty text on pages: {empty_pages}")

            low_conf_pages = [
                p for p, c in doc.ocr_avg_conf_by_page.items() if isinstance(c, (int, float)) and c < 40.0
            ]
            if low_conf_pages:
                doc.errors.append(f"Low OCR confidence on pages: {sorted(low_conf_pages)}")

            if skip_llm:
                try:
                    doc.extracted = extract_fields_with_rules(pages_text)
                    doc.errors.append("skip_llm=true: used rule-based extraction")
                except Exception as e:  # noqa: BLE001
                    doc.errors.append(f"Rule-based extraction failed: {type(e).__name__}: {e}")
            else:
                try:
                    doc.extracted = extract_fields_with_llm(pages_text)
                except Exception as e:  # noqa: BLE001
                    doc.errors.append(f"LLM extraction failed: {type(e).__name__}: {e}")
                    try:
                        doc.extracted = extract_fields_with_rules(pages_text)
                        doc.errors.append("Fell back to rule-based extraction")
                    except Exception as e2:  # noqa: BLE001
                        doc.errors.append(f"Rule-based extraction failed: {type(e2).__name__}: {e2}")

            if doc.extracted:
                try:
                    route = compute_route_miles(
                        origin=doc.extracted.pickup_location,
                        destination=doc.extracted.delivery_location,
                        cache=cache,
                    )
                    doc.route = route
                except Exception as e:  # noqa: BLE001
                    doc.errors.append(f"Routing failed: {type(e).__name__}: {e}")
                    route = None

                if route and route.miles and route.miles > 0 and doc.extracted.total_rate_usd > 0:
                    doc.rate_per_mile = doc.extracted.total_rate_usd / route.miles
                else:
                    doc.errors.append("Could not compute rate_per_mile (missing miles or total_rate_usd).")

                if not route or not route.miles:
                    doc.errors.append("Route miles missing from routing provider.")
                if doc.extracted.total_rate_usd <= 0:
                    doc.errors.append("total_rate_usd is <= 0.")
                if len(doc.extracted.pickup_location.strip()) < 4 or len(doc.extracted.delivery_location.strip()) < 4:
                    doc.errors.append("Pickup/delivery location too short (likely OCR/LLM issue).")
        except Exception as e:  # noqa: BLE001
            doc.errors.append(f"Unhandled error: {type(e).__name__}: {e}")

        results.append(doc)

    return results

