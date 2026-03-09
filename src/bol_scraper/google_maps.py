from __future__ import annotations

import os
from typing import Any, Optional

import requests

from bol_scraper.cache import Cache
from bol_scraper.models import GeoPoint, RouteResult


def _key_optional() -> str | None:
    return os.getenv("GOOGLE_MAPS_API_KEY") or None


def _cache_key(prefix: str, *parts: str) -> str:
    joined = "|".join(p.strip().lower() for p in parts)
    return f"{prefix}:{joined}"


def geocode(address: str, *, cache: Cache) -> tuple[str, Optional[GeoPoint], dict[str, Any]]:
    api_key = _key_optional()
    key = _cache_key("geocode_google" if api_key else "geocode_osm", address)
    cached = cache.get_json(key)
    if cached and cached.get("point"):
        pt = cached["point"]
        return cached["formatted"], GeoPoint(**pt), cached

    formatted = address
    point: Optional[GeoPoint] = None

    if api_key:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": api_key}
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("results"):
            res = data["results"][0]
            formatted = res.get("formatted_address") or address
            loc = (res.get("geometry") or {}).get("location") or None
            if loc and "lat" in loc and "lng" in loc:
                point = GeoPoint(lat=float(loc["lat"]), lng=float(loc["lng"]))
        raw = {"provider": "google", "status": data.get("status"), "results_len": len(data.get("results") or [])}
    else:
        # Nominatim fallback (rate-limited; cached to minimize calls)
        url = "https://nominatim.openstreetmap.org/search"
        headers = {"User-Agent": "bol-scraper/0.1 (local)"}

        def _queries(a: str) -> list[str]:
            a = " ".join(a.split()).strip()
            simplified = (
                a.replace("Type Reference #", "")
                .replace("Reference #", "")
                .replace("we Reference #", "")
                .replace("#", "")
            )
            simplified = " ".join(simplified.split()).strip(" ,")
            parts = [a]
            if simplified and simplified != a:
                parts.append(simplified)
            if "," in simplified:
                parts.append(simplified.split(",")[-2].strip() + ", " + simplified.split(",")[-1].strip())
            return [p for p in parts if p]

        data = []
        used_q = address
        for q in _queries(address):
            used_q = q
            params = {"q": q, "format": "json", "limit": 1, "countrycodes": "us"}
            r = requests.get(url, params=params, timeout=30, headers=headers)
            r.raise_for_status()
            data = r.json() or []
            if data:
                break

        if data:
            res = data[0]
            formatted = res.get("display_name") or used_q
            if res.get("lat") and res.get("lon"):
                point = GeoPoint(lat=float(res["lat"]), lng=float(res["lon"]))
        raw = {"provider": "nominatim", "results_len": len(data or []), "query": used_q}

    payload = {
        "formatted": formatted,
        "point": point.model_dump() if point else None,
        "raw": raw,
    }
    cache.set_json(key, payload)
    return formatted, point, payload


def directions_miles(
    origin: str,
    destination: str,
    *,
    cache: Cache,
) -> tuple[Optional[float], dict[str, Any]]:
    api_key = _key_optional()
    key = _cache_key("directions_google" if api_key else "directions_osrm", origin, destination)
    cached = cache.get_json(key)
    if cached and cached.get("miles") is not None:
        return cached["miles"], cached

    miles: Optional[float] = None

    if api_key:
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            "origin": origin,
            "destination": destination,
            "key": api_key,
            "units": "imperial",
        }
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        routes = data.get("routes") or []
        if routes:
            legs = routes[0].get("legs") or []
            meters = 0
            for leg in legs:
                dist = (leg.get("distance") or {}).get("value")
                if isinstance(dist, (int, float)):
                    meters += float(dist)
            if meters > 0:
                miles = meters / 1609.344
        raw = {"provider": "google", "status": data.get("status"), "routes_len": len(routes)}
    else:
        # OSRM public endpoint fallback. Expects origin/destination already geocoded to "lat,lng" strings.
        # We accept origin/destination as addresses and let caller pass formatted address; for OSRM we need coordinates,
        # so this path is only used by compute_route_miles() when points are available.
        raw = {"provider": "osrm", "status": "skipped_needs_points"}

    payload = {"miles": miles, "raw": raw}
    cache.set_json(key, payload)
    return miles, payload


def compute_route_miles(
    *,
    origin: str,
    destination: str,
    cache: Cache,
) -> RouteResult:
    origin_fmt, origin_pt, origin_raw = geocode(origin, cache=cache)
    dest_fmt, dest_pt, dest_raw = geocode(destination, cache=cache)

    api_key = _key_optional()
    if api_key:
        miles, dir_raw = directions_miles(origin_fmt, dest_fmt, cache=cache)
        provider = "google"
    else:
        if not origin_pt or not dest_pt:
            raise RuntimeError("Routing fallback requires successful geocoding for both endpoints.")
        key = _cache_key("osrm_route", f"{origin_pt.lat},{origin_pt.lng}", f"{dest_pt.lat},{dest_pt.lng}")
        cached = cache.get_json(key)
        if cached and cached.get("miles") is not None:
            miles = cached["miles"]
            dir_raw = cached
        else:
            url = (
                "https://router.project-osrm.org/route/v1/driving/"
                f"{origin_pt.lng},{origin_pt.lat};{dest_pt.lng},{dest_pt.lat}"
            )
            params = {"overview": "false"}
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            meters = None
            routes = data.get("routes") or []
            if routes:
                meters = routes[0].get("distance")
            miles = (float(meters) / 1609.344) if meters else None
            dir_raw = {"miles": miles, "raw": {"provider": "osrm", "code": data.get("code")}}
            cache.set_json(key, dir_raw)
        provider = "osrm"

    return RouteResult(
        origin=origin_fmt,
        destination=dest_fmt,
        origin_point=origin_pt,
        destination_point=dest_pt,
        miles=miles,
        provider=provider,
        raw_summary={
            "geocode_origin": origin_raw.get("raw"),
            "geocode_destination": dest_raw.get("raw"),
            "directions": dir_raw.get("raw"),
        },
    )

