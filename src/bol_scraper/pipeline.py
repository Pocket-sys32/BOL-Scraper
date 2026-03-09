from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from bol_scraper.cache import Cache
from bol_scraper.config import ENABLE_GOOGLE, ENABLE_LLM, MAX_LOCAL_MILES, MIN_GOOGLE_RATE_USD, OCR_CONF_THRESHOLD
from bol_scraper.extract_llm import extract_fields_with_llm
from bol_scraper.extract_rules import extract_fields_with_rules
from bol_scraper.google_maps import compute_route_miles
from bol_scraper.models import DocumentResult
from bol_scraper.ocr import ocr_pdf_to_pages_text

console = Console()


def _evaluate_quality_before_routing(doc: DocumentResult) -> None:
    """
    Set doc.needs_llm based on OCR quality and rule-based extraction completeness.
    Assumes doc.extracted may or may not be populated yet.
    """
    # OCR quality check
    low_conf_pages = [
        p for p, c in doc.ocr_avg_conf_by_page.items() if isinstance(c, (int, float)) and c < OCR_CONF_THRESHOLD
    ]
    if low_conf_pages:
        doc.errors.append(f"Low OCR confidence on pages: {sorted(low_conf_pages)}")

    # If we don't even have extracted fields yet, we likely need LLM.
    if not doc.extracted:
        doc.needs_llm = True
        return

    ex = doc.extracted

    # Basic presence checks
    missing_fields: list[str] = []
    if not ex.pickup_location.strip():
        missing_fields.append("pickup_location")
    if not ex.delivery_location.strip():
        missing_fields.append("delivery_location")
    if ex.total_rate_usd <= 0:
        missing_fields.append("total_rate_usd")
    if missing_fields:
        doc.errors.append(f"Missing or invalid fields from rules: {missing_fields}")
        doc.needs_llm = True

    # Heuristic quality: locations should be at least a few characters and include a state-like token.
    for label, loc in (("pickup_location", ex.pickup_location), ("delivery_location", ex.delivery_location)):
        loc_clean = loc.strip()
        if len(loc_clean) < 6 or "," not in loc_clean:
            doc.errors.append(f"{label} looks too short or unstructured: {loc_clean!r}")
            doc.needs_llm = True


def _evaluate_quality_after_routing(doc: DocumentResult) -> None:
    """
    After a routing attempt, decide whether we should escalate to a paid routing API.
    """
    if not doc.extracted or not doc.route:
        return

    route = doc.route
    ex = doc.extracted

    miles = route.miles or 0.0
    if miles <= 0:
        doc.errors.append("Route miles missing or zero from free routing provider.")
        doc.needs_paid_routing = True
        return

    if miles > MAX_LOCAL_MILES:
        doc.errors.append(f"Route miles {miles:.1f} exceed MAX_LOCAL_MILES={MAX_LOCAL_MILES}.")
        doc.needs_paid_routing = True

    if ex.total_rate_usd >= MIN_GOOGLE_RATE_USD and route.provider != "google":
        # High-value load: consider Google if enabled.
        doc.needs_paid_routing = True


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

            # 1) Always try rule-based extraction first.
            try:
                doc.extracted = extract_fields_with_rules(pages_text)
                doc.extraction_path = "rules"
            except Exception as e:  # noqa: BLE001
                doc.errors.append(f"Rule-based extraction failed: {type(e).__name__}: {e}")

            # 2) Decide if we should escalate to LLM.
            _evaluate_quality_before_routing(doc)
            if not skip_llm and ENABLE_LLM and doc.needs_llm:
                try:
                    llm_fields = extract_fields_with_llm(pages_text)
                    doc.extracted = llm_fields
                    doc.extraction_path = "llm" if doc.extraction_path is None else "rules+llm"
                except Exception as e:  # noqa: BLE001
                    doc.errors.append(f"LLM extraction failed: {type(e).__name__}: {e}")

            # 3) Routing: always try free/primary routing provider first.
            if doc.extracted:
                try:
                    route = compute_route_miles(
                        origin=doc.extracted.pickup_location,
                        destination=doc.extracted.delivery_location,
                        cache=cache,
                    )
                    doc.route = route
                    doc.routing_provider_effective = route.provider
                except Exception as e:  # noqa: BLE001
                    doc.errors.append(f"Routing failed: {type(e).__name__}: {e}")
                    route = None

                # Evaluate whether to escalate to paid routing (Google) based on OSRM result.
                _evaluate_quality_after_routing(doc)

                # If OSRM/Nominatim path was used and marked as needing paid routing, and Google is enabled,
                # a second pass will occur automatically on a subsequent run when GOOGLE_MAPS_API_KEY is present.
                # We do not auto-call Google here if ENABLE_GOOGLE is false.

                if route and route.miles and route.miles > 0 and doc.extracted.total_rate_usd > 0:
                    doc.rate_per_mile = doc.extracted.total_rate_usd / route.miles
                else:
                    doc.errors.append("Could not compute rate_per_mile (missing miles or total_rate_usd).")

                if doc.extracted.total_rate_usd <= 0:
                    doc.errors.append("total_rate_usd is <= 0.")
                if len(doc.extracted.pickup_location.strip()) < 4 or len(doc.extracted.delivery_location.strip()) < 4:
                    doc.errors.append("Pickup/delivery location too short (likely OCR/LLM issue).")
        except Exception as e:  # noqa: BLE001
            doc.errors.append(f"Unhandled error: {type(e).__name__}: {e}")

        results.append(doc)

    return results

