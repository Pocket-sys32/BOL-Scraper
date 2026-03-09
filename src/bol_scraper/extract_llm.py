from __future__ import annotations

import json
import os
import re
from datetime import date

from openai import OpenAI
from pydantic import ValidationError

from bol_scraper.models import ExtractedFields, FieldEvidence


_SYSTEM = """You extract fields from OCR text of trucking paperwork (BOL/rate confirmation/invoice pages).

Return ONLY valid JSON that matches the schema. Use best effort even if OCR is noisy.
Evidence must be short verbatim quotes from the provided text, and page numbers are 1-based.
"""


def _pages_to_prompt(pages_text: list[str]) -> str:
    parts: list[str] = []
    for i, t in enumerate(pages_text, start=1):
        t = (t or "").strip()
        parts.append(f"--- PAGE {i} ---\n{t}\n")
    return "\n".join(parts)


def _strip_to_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("LLM did not return JSON object.")
    return m.group(0)


def _schema() -> dict:
    # Minimal JSON Schema for robust structured output.
    return {
        "name": "bol_extraction",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "pickup_location": {"type": "string"},
                "delivery_location": {"type": "string"},
                "pickup_date": {"type": "string", "description": "ISO date (YYYY-MM-DD)"},
                "delivery_date": {"type": "string", "description": "ISO date (YYYY-MM-DD)"},
                "total_rate_usd": {"type": "number"},
                "pickup_location_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"page": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["page", "quote"],
                },
                "delivery_location_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"page": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["page", "quote"],
                },
                "pickup_date_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"page": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["page", "quote"],
                },
                "delivery_date_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"page": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["page", "quote"],
                },
                "total_rate_usd_evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"page": {"type": "integer"}, "quote": {"type": "string"}},
                    "required": ["page", "quote"],
                },
            },
            "required": [
                "pickup_location",
                "delivery_location",
                "pickup_date",
                "delivery_date",
                "total_rate_usd",
                "pickup_location_evidence",
                "delivery_location_evidence",
                "pickup_date_evidence",
                "delivery_date_evidence",
                "total_rate_usd_evidence",
            ],
        },
    }


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def extract_fields_with_llm(pages_text: list[str]) -> ExtractedFields:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)

    user = (
        "Extract:\n"
        "- pickup_location (city, state or full address)\n"
        "- delivery_location\n"
        "- pickup_date (YYYY-MM-DD)\n"
        "- delivery_date (YYYY-MM-DD)\n"
        "- total_rate_usd (overall load rate, in USD)\n\n"
        "Also include evidence objects for each field with {page, quote}.\n\n"
        "Here is the OCR/text by page:\n\n"
        f"{_pages_to_prompt(pages_text)}"
    )

    # Prefer strict structured output when available; fall back to plain JSON parsing otherwise.
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_schema", "json_schema": _schema()},
        )
        content = resp.output_text
    except Exception:  # noqa: BLE001
        chat = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        content = chat.choices[0].message.content or ""

    raw_json = _strip_to_json(content)
    payload = json.loads(raw_json)

    # Coerce dates into date objects explicitly to get clearer errors.
    try:
        payload["pickup_date"] = _parse_date(payload["pickup_date"])
        payload["delivery_date"] = _parse_date(payload["delivery_date"])
        for k in [
            "pickup_location_evidence",
            "delivery_location_evidence",
            "pickup_date_evidence",
            "delivery_date_evidence",
            "total_rate_usd_evidence",
        ]:
            payload[k] = FieldEvidence.model_validate(payload[k])
        return ExtractedFields.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"LLM extraction did not match schema: {e}") from e

