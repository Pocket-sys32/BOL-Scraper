from __future__ import annotations

import csv
from pathlib import Path

from bol_scraper.models import DocumentResult


def export_csv(results: list[DocumentResult], out_csv: Path) -> None:
    fieldnames = [
        "source_path",
        "pickup_location",
        "delivery_location",
        "pickup_date",
        "delivery_date",
        "total_rate_usd",
        "route_miles",
        "rate_per_mile",
        "extraction_path",
        "routing_provider",
        "errors",
    ]

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            extracted = r.extracted
            miles = r.route.miles if r.route else None
            w.writerow(
                {
                    "source_path": r.source_path,
                    "pickup_location": extracted.pickup_location if extracted else None,
                    "delivery_location": extracted.delivery_location if extracted else None,
                    "pickup_date": extracted.pickup_date.isoformat() if extracted else None,
                    "delivery_date": extracted.delivery_date.isoformat() if extracted else None,
                    "total_rate_usd": extracted.total_rate_usd if extracted else None,
                    "route_miles": miles,
                    "rate_per_mile": r.rate_per_mile,
                    "extraction_path": r.extraction_path,
                    "routing_provider": r.routing_provider_effective,
                    "errors": " | ".join(r.errors) if r.errors else "",
                }
            )

