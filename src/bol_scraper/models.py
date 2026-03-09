from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class FieldEvidence(BaseModel):
    page: int = Field(..., ge=1, description="1-based page number")
    quote: str = Field(..., min_length=1, description="Short supporting quote from OCR/text")


class ExtractedFields(BaseModel):
    pickup_location: str
    delivery_location: str
    pickup_date: date
    delivery_date: date
    total_rate_usd: float

    pickup_location_evidence: FieldEvidence
    delivery_location_evidence: FieldEvidence
    pickup_date_evidence: FieldEvidence
    delivery_date_evidence: FieldEvidence
    total_rate_usd_evidence: FieldEvidence


class GeoPoint(BaseModel):
    lat: float
    lng: float


class RouteResult(BaseModel):
    origin: str
    destination: str
    origin_point: Optional[GeoPoint] = None
    destination_point: Optional[GeoPoint] = None
    miles: Optional[float] = None
    provider: str = "google"
    raw_summary: Optional[dict[str, Any]] = None


class DocumentResult(BaseModel):
    source_path: str
    page_count: int

    embedded_text_used_pages: list[int] = Field(default_factory=list)
    ocr_used_pages: list[int] = Field(default_factory=list)
    ocr_avg_conf_by_page: dict[int, float] = Field(default_factory=dict, description="1-based page -> avg OCR confidence (0-100)")

    extracted: Optional[ExtractedFields] = None
    route: Optional[RouteResult] = None

    rate_per_mile: Optional[float] = None
    errors: list[str] = Field(default_factory=list)

    # Cost/decision analytics
    extraction_path: str | None = Field(
        default=None,
        description='Which extractor produced "extracted": "rules", "llm", or "rules+llm".',
    )
    routing_provider_effective: str | None = Field(
        default=None,
        description='Which routing provider was actually used for miles: "osrm" or "google".',
    )

    # Internal decision flags
    needs_llm: bool = False
    needs_paid_routing: bool = False

