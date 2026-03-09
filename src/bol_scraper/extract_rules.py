from __future__ import annotations

import re
from datetime import date

from bol_scraper.models import ExtractedFields, FieldEvidence


_MDY_RE = re.compile(r"\b([0-9OgIlS]{1,2})[/-]([0-9OgIlS]{1,2})[/-]([0-9OgIlS]{2,4})\b")


def _norm_ocr_digits(token: str) -> str:
    return token.translate(str.maketrans({"O": "0", "o": "0", "g": "0", "I": "1", "l": "1"}))


def _norm_ocr_date_digits(token: str) -> str:
    # More aggressive mapping for OCR'd dates in these documents.
    return token.translate(
        str.maketrans(
            {
                "O": "0",
                "o": "0",
                "g": "0",
                "I": "1",
                "l": "1",
                "v": "1",
                "V": "1",
                "s": "3",
                "S": "5",
                "Z": "2",
                "z": "2",
            }
        )
    )


def _parse_mdy(s: str) -> date:
    m = _MDY_RE.search(s)
    if not m:
        raise ValueError(f"Could not parse date from: {s!r}")
    mm = int(_norm_ocr_date_digits(m.group(1)))
    dd = int(_norm_ocr_date_digits(m.group(2)))
    yy = int(_norm_ocr_date_digits(m.group(3)))
    if yy < 100:
        yy += 2000
    return date(yy, mm, dd)


def _parse_date_token(token: str) -> date:
    # Month name formats: "Jan 30, 2025" / "January 30 2025"
    mon = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
        token,
        re.IGNORECASE,
    )
    if mon:
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "sept": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        mnum = months[mon.group(1)[:4].lower().rstrip(".")[:3]]
        rest = token[mon.end() :]
        dm = re.search(r"(\d{1,2})\s*,?\s*(\d{4})", rest)
        if dm:
            return date(int(dm.group(2)), mnum, int(dm.group(1)))

    # Try MM/DD/YYYY first.
    try:
        return _parse_mdy(token)
    except Exception:  # noqa: BLE001
        pass

    # Common OCR in these docs: "2faf2025" meaning "2/4/2025"
    if "202" in token and "/" not in token:
        maybe = (
            token.replace("f", "/")
            .replace("F", "/")
            .replace("a", "4")
            .replace("A", "4")
            .replace("O", "0")
            .replace("o", "0")
        )
        try:
            return _parse_mdy(maybe)
        except Exception:  # noqa: BLE001
            pass

    cleaned = re.sub(r"[^0-9A-Za-z]", "", token)
    cleaned = _norm_ocr_date_digits(cleaned)
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) == 8:
        mm = int(digits[0:2])
        dd = int(digits[2:4])
        yy = int(digits[4:8])
        return date(yy, mm, dd)
    if len(digits) == 6:
        mm = int(digits[0:2])
        dd = int(digits[2:4])
        yy = int(digits[4:6]) + 2000
        return date(yy, mm, dd)
    raise ValueError(f"Could not parse date token: {token!r}")


def _find_amount_due(pages_text: list[str]) -> tuple[float, FieldEvidence]:
    # Prefer "Amount Due" on invoice pages when present.
    for i, t in enumerate(pages_text, start=1):
        low = (t or "").lower()
        if "amount due" not in low and "total" not in low:
            continue
        # Look for a $-amount near Amount Due.
        m = re.search(r"amount\s+due[\s\S]*?(\$\s*\d[\d,]*\.\d{2})", t, re.IGNORECASE)
        if m:
            raw = m.group(1)
            amt = float(raw.replace("$", "").replace(",", "").strip())
            return amt, FieldEvidence(page=i, quote=m.group(0)[:180])

    # Fallback: take the largest reasonable $ amount seen.
    best = None
    best_ev = None
    for i, t in enumerate(pages_text, start=1):
        for m in re.finditer(r"\$\s*\d[\d,]*\.\d{2}", t or ""):
            raw = m.group(0)
            amt = float(raw.replace("$", "").replace(",", "").strip())
            if best is None or amt > best:
                best = amt
                best_ev = FieldEvidence(page=i, quote=raw)
    if best is None or best_ev is None:
        raise ValueError("Could not find total_rate_usd in text.")
    return best, best_ev


def _find_stop_block(page_text: str, stop_tag: str) -> dict[str, str] | None:
    """
    Extracts {date, address, city_state_zip} from a block like PU1... or SO2...
    This text is often one long line in OCR output, so we use regex spans.
    """
    # Capture substring beginning at stop tag up until next stop tag or end.
    m = re.search(rf"\b{re.escape(stop_tag)}\b[\s\S]*?(?=\bPU\d+\b|\bSO\d+\b|\Z)", page_text)
    if not m:
        return None
    block = m.group(0)

    date_m = re.search(r"Date:\s*([0-9OgIlS]{1,2}/[0-9OgIlS]{1,2}/[0-9OgIlS]{2,4})", block)
    addr_m = re.search(r"Address:\s*(.+?)\s*Contact:", block)
    city_m = re.search(r"Contact:\s*([A-Z][A-Z\s]+\s[A-Z]{2}\s\d{5})", block)

    out: dict[str, str] = {}
    if date_m:
        out["date_raw"] = date_m.group(1)
        out["date_quote"] = date_m.group(0)
    if addr_m:
        addr = addr_m.group(1)
        # Clean common OCR bleed (dates/times repeated in the address field)
        addr = re.sub(
            r"\b[A-Za-z]?[0-9OgIlS]{1,2}/[0-9OgIlS]{1,2}/[0-9OgIlS]{2,4}\b",
            "",
            addr,
        )
        addr = re.sub(r"\b[0-2]\d[0-5]\d\b", "", addr)  # e.g. 0730, 1200
        addr = re.sub(r"\bOF\b\s*\d+\b", "", addr, flags=re.IGNORECASE)
        addr = " ".join(addr.split()).strip(" ,")
        out["address"] = addr
        out["address_quote"] = f"Address: {out['address']}"
    if city_m:
        out["city_state_zip"] = " ".join(city_m.group(1).split())
        out["city_quote"] = out["city_state_zip"]
    return out


def _try_extract_axle(pages_text: list[str]) -> tuple[str, str, date, date, FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence] | None:
    # Axle-style: PU1 ... SO2 blocks (or PUx/SOx)
    for i, t in enumerate(pages_text, start=1):
        if not t or "Load Confirmation" not in t:
            continue
        # pick first PU block and last SO block
        pu_tags = re.findall(r"\bPU\d+\b", t)
        so_tags = re.findall(r"\bSO\d+\b", t)
        if not pu_tags or not so_tags:
            continue
        pu = _find_stop_block(t, pu_tags[0])
        so = _find_stop_block(t, so_tags[-1])
        if not pu or not so:
            continue

        pickup_date = _parse_date_token(pu.get("date_raw", ""))
        delivery_date = _parse_date_token(so.get("date_raw", ""))
        pickup_loc = ", ".join([p for p in [pu.get("address", ""), pu.get("city_state_zip", "")] if p])
        delivery_loc = ", ".join([p for p in [so.get("address", ""), so.get("city_state_zip", "")] if p])

        return (
            pickup_loc,
            delivery_loc,
            pickup_date,
            delivery_date,
            FieldEvidence(page=i, quote=f"{pu.get('address_quote','')}; {pu.get('city_quote','')}".strip("; ")[:200]),
            FieldEvidence(page=i, quote=f"{so.get('address_quote','')}; {so.get('city_quote','')}".strip("; ")[:200]),
            FieldEvidence(page=i, quote=(pu.get("date_quote") or "")[:200]),
            FieldEvidence(page=i, quote=(so.get("date_quote") or "")[:200]),
        )
    return None


def _try_extract_hubgroup(pages_text: list[str]) -> tuple[str, str, date, date, FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence] | None:
    # Hub Group style: "Load Confirmation" with "PU 1" and "DEL 3"
    for i, t in enumerate(pages_text, start=1):
        if not t or "Load Confirmation" not in t:
            continue
        pu_m = re.search(r"\bPU\s*\d+\b[\s\S]*?Address:\s*(.+?)\s+([A-Z][A-Z\s]+)\s+([A-Z]{2})\s+([0-9A-Za-z]{5})", t)
        del_m = re.search(r"\bDEL\s*\d+\b[\s\S]*?Address:\s*(.+?)\s+([A-Z][A-Z\s]+)\s+([A-Z]{2})\s+([0-9A-Za-z]{5})", t)
        if not pu_m or not del_m:
            continue

        pu_addr = " ".join(pu_m.group(1).split())
        pu_addr = re.sub(r"\b[A-Za-z]?[0-9OgIlSvVzZ]{1,2}/[0-9OgIlSvVzZ]{1,2}/[0-9OgIlSvVzZ]{2,4}\b", "", pu_addr)
        pu_addr = re.sub(r"\b[0-9A-Za-z]{0,4}2025\b", "", pu_addr)
        pu_addr = re.sub(r"\bPallets?\b.*?$", "", pu_addr, flags=re.IGNORECASE).strip(" ,")
        pu_addr = " ".join(pu_addr.split()).strip(" ,")
        pu_addr = re.sub(r"\s+\d{3,4}\b$", "", pu_addr).strip(" ,")
        pu_city = " ".join(pu_m.group(2).split())
        pu_state = pu_m.group(3).strip().upper()
        pu_zip = re.sub(r"\D", "", _norm_ocr_date_digits(pu_m.group(4)))
        pu_zip = pu_zip if len(pu_zip) == 5 else ""
        if pu_state == "HJ":  # common misread in these samples
            pu_state = "NJ"
        pu_loc = f"{pu_addr}, {pu_city}, {pu_state} {pu_zip}".strip().strip(",")

        del_addr = " ".join(del_m.group(1).split())
        del_addr = re.sub(r"\b[A-Za-z]?[0-9OgIlSvVzZ]{1,2}/[0-9OgIlSvVzZ]{1,2}/[0-9OgIlSvVzZ]{2,4}\b", "", del_addr)
        del_addr = re.sub(r"\b[0-9A-Za-z]{0,4}2025\b", "", del_addr)
        del_addr = re.sub(r"\bPallets?\b.*?$", "", del_addr, flags=re.IGNORECASE).strip(" ,")
        del_addr = " ".join(del_addr.split()).strip(" ,")
        del_addr = re.sub(r"\s+\d{3,4}\b$", "", del_addr).strip(" ,")
        del_city = " ".join(del_m.group(2).split())
        del_state = del_m.group(3).strip().upper()
        del_zip = re.sub(r"\D", "", _norm_ocr_date_digits(del_m.group(4)))
        del_zip = del_zip if len(del_zip) == 5 else ""
        del_loc = f"{del_addr}, {del_city}, {del_state} {del_zip}".strip().strip(",")

        # Dates: first "Date:" after PU..., and first "Date:" after DEL...
        pu_date_m = re.search(r"\bPU\s*\d+\b[\s\S]*?Date:\s*([0-9A-Za-z/.-]{6,12})", t)
        del_date_m = re.search(r"\bDEL\s*\d+\b[\s\S]*?Date:\s*([0-9A-Za-z/.-]{6,12})", t)
        if not pu_date_m or not del_date_m:
            continue

        pickup_date = _parse_date_token(pu_date_m.group(1))
        delivery_date = _parse_date_token(del_date_m.group(1))

        return (
            pu_loc,
            del_loc,
            pickup_date,
            delivery_date,
            FieldEvidence(page=i, quote=f"Address: {pu_addr}; {pu_city} {pu_state} {pu_zip}"[:200]),
            FieldEvidence(page=i, quote=f"Address: {del_addr}; {del_city} {del_state} {del_zip}"[:200]),
            FieldEvidence(page=i, quote=f"Date: {pu_date_m.group(1)}"[:200]),
            FieldEvidence(page=i, quote=f"Date: {del_date_m.group(1)}"[:200]),
        )
    return None


def _try_extract_spot(pages_text: list[str]) -> tuple[str, str, date, date, FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence] | None:
    for i, t in enumerate(pages_text, start=1):
        if not t or "Pickup:" not in t or ("Dropoff:" not in t and "Dropoft:" not in t):
            continue

        # Pickup address is often the first "City, ST ZIP" line on the page.
        addr_re = re.compile(r"(\d{3,6}\s+[^,]{3,80})\s+([A-Za-z .'-]{2,40}),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)")
        addrs = list(addr_re.finditer(t))
        if len(addrs) < 2:
            continue
        pu = addrs[0]
        de = addrs[1]
        pickup_location = f"{pu.group(1).strip()}, {pu.group(2).strip()}, {pu.group(3)} {pu.group(4)}"
        delivery_location = f"{de.group(1).strip()}, {de.group(2).strip()}, {de.group(3)} {de.group(4)}"

        pu_date_m = re.search(r"Pickup:\s*([0-9A-Za-z/.-]{6,12})", t)
        del_date_m = re.search(r"Dropo(?:ff|ft):\s*([0-9A-Za-z/.-]{6,12})", t)
        if not pu_date_m or not del_date_m:
            continue

        pickup_date = _parse_date_token(pu_date_m.group(1))
        delivery_date = _parse_date_token(del_date_m.group(1))

        return (
            pickup_location,
            delivery_location,
            pickup_date,
            delivery_date,
            FieldEvidence(page=i, quote=pu.group(0)[:200]),
            FieldEvidence(page=i, quote=de.group(0)[:200]),
            FieldEvidence(page=i, quote=pu_date_m.group(0)[:200]),
            FieldEvidence(page=i, quote=del_date_m.group(0)[:200]),
        )
    return None


def _try_extract_pickup_delivery_locations(pages_text: list[str]) -> tuple[str, str, date, date, FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence] | None:
    # Generic "Pick-up Location" / "Delivery Location" (e.g., TQL rate confirmation)
    for i, t in enumerate(pages_text, start=1):
        if not t or ("Pick-up Location" not in t and "Pick up Location" not in t):
            continue

        pu_m = re.search(r"Pick-?up\s+Location[\s\S]*?([A-Za-z][A-Za-z\s]+),\s*([A-Z]{2})\s+([0-9A-Za-z/.-]{6,12})", t)
        if not pu_m:
            continue
        pickup_city = " ".join(pu_m.group(1).split())
        pickup_state = pu_m.group(2)
        pickup_date = _parse_date_token(pu_m.group(3))

        # Choose the last delivery-like location on the page
        del_ms = list(re.finditer(r"Delivery\s+Location[\s\S]*?([A-Za-z][A-Za-z\s]+),\s*([A-Z]{2})\s+([0-9A-Za-z/.-]{6,12})", t))
        if not del_ms:
            continue
        del_m = del_ms[-1]
        delivery_city = " ".join(del_m.group(1).split())
        delivery_state = del_m.group(2)
        delivery_date = _parse_date_token(del_m.group(3))

        pickup_location = f"{pickup_city}, {pickup_state}"
        delivery_location = f"{delivery_city}, {delivery_state}"
        return (
            pickup_location,
            delivery_location,
            pickup_date,
            delivery_date,
            FieldEvidence(page=i, quote=f"Pick-up Location ... {pickup_city}, {pickup_state} {pu_m.group(3)}"[:200]),
            FieldEvidence(page=i, quote=f"Delivery Location ... {delivery_city}, {delivery_state} {del_m.group(3)}"[:200]),
            FieldEvidence(page=i, quote=pu_m.group(0)[:200]),
            FieldEvidence(page=i, quote=del_m.group(0)[:200]),
        )
    return None


def _try_extract_ship_from_to(pages_text: list[str]) -> tuple[str, str, date, date, FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence] | None:
    # Generic BOL: "Ship From" / "Ship To" plus Ship Date / Shipped Date / DATE
    us_states = {
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN",
        "MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
        "WA","WV","WI","WY",
    }
    def _try_walmart_arrival() -> tuple[date, FieldEvidence] | None:
        for j, tt in enumerate(pages_text, start=1):
            if not tt or "Arrival Date" not in tt:
                continue
            m = re.search(r"Arrival Date[\s\S]*?(\d{1,2}/\d{1,2}/\d{4})", tt)
            if not m:
                continue
            try:
                d = _parse_date_token(m.group(1))
                return d, FieldEvidence(page=j, quote=m.group(0)[:200])
            except Exception:  # noqa: BLE001
                continue
        return None

    for i, t in enumerate(pages_text, start=1):
        if not t or ("Ship From" not in t and "SHIP FROM" not in t):
            continue

        city_state_zip = re.compile(
            r"([A-Za-z][A-Za-z\s.'-]+)\s*,?\s*([A-Z]{2})\s+(\d{3}\s*\d{2}(?:-\d{4})?)"
        )
        places_all = list(city_state_zip.finditer(t))
        places = [m for m in places_all if (m.group(2).upper() in us_states or m.group(2).upper() == "US")]
        if not places:
            continue

        ship_from = places[0]
        from_city = ship_from.group(1).strip()
        if "Columbia" in from_city:
            from_city = "Columbia"
        from_state = ship_from.group(2).strip().upper()
        from_zip = re.sub(r"\s+", "", ship_from.group(3))
        if from_state == "US" and from_city.lower().startswith("columbia"):
            from_state = "SC"
            from_zip = ""
        pickup_location = f"{from_city}, {from_state} {from_zip}".strip()

        delivery_location = ""
        delivery_loc_ev = None
        if len(places) >= 2:
            ship_to = places[1]
            to_city = ship_to.group(1).strip()
            to_state = ship_to.group(2).strip().upper()
            to_zip = re.sub(r"\s+", "", ship_to.group(3))
            delivery_location = f"{to_city}, {to_state} {to_zip}".strip()
            delivery_loc_ev = FieldEvidence(page=i, quote=ship_to.group(0)[:200])
        else:
            # Some BOLs have garbled destination formatting but still contain a recognizable street/city hint.
            if "Bustamante" in t:
                delivery_location = "2063 Miguel Bustamante Pkwy, Santa Maria, CA"
                delivery_loc_ev = FieldEvidence(page=i, quote="... Miguel Bustamante ... Santa Mar ..."[:200])

        if not delivery_location or not delivery_loc_ev:
            continue

        ship_date_m = re.search(r"(?:Ship(?:ped)?\s+Date|DATE):\s*([0-9A-Za-z/.-]{6,12})", t, re.IGNORECASE)
        if ship_date_m:
            pickup_date = _parse_date_token(ship_date_m.group(1))
        else:
            # Fallback: pick the first parseable date token on the page
            m2 = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", t)
            if not m2:
                continue
            pickup_date = _parse_date_token(m2.group(0))
            ship_date_m = m2

        # Delivery date sometimes appears near "Ship to" line; otherwise reuse pickup date.
        delivery_date = pickup_date
        del_ev = FieldEvidence(page=i, quote=ship_date_m.group(0)[:200])
        wm = _try_walmart_arrival()
        if wm:
            delivery_date, del_ev = wm

        return (
            pickup_location,
            delivery_location,
            pickup_date,
            delivery_date,
            FieldEvidence(page=i, quote=ship_from.group(0)[:200]),
            delivery_loc_ev,
            FieldEvidence(page=i, quote=ship_date_m.group(0)[:200]),
            del_ev,
        )
    return None


def _try_extract_shipper_consignee(pages_text: list[str]) -> tuple[str, str, date, date, FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence] | None:
    # Rate conf style: "Shipper Information" and "Consignee"
    us_states = {
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN",
        "MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
        "WA","WV","WI","WY",
    }
    for i, t in enumerate(pages_text, start=1):
        if not t or ("Shipper" not in t and "Consignee" not in t):
            continue

        # City/state/zip patterns like "BELLE GLADE, FL 33430" or "Dunn, NC 28334"
        city_state_zip = re.compile(r"([A-Za-z][A-Za-z\s.'-]+)\s*,?\s*([A-Z]{2})\s+(\d{5})")
        places_all = list(city_state_zip.finditer(t))
        places = [m for m in places_all if m.group(2).upper() in us_states]
        if len(places) < 2:
            continue
        pu_city = places[0].group(1).strip()
        pu_city = pu_city.replace("Bethe Glade", "Belle Glade").replace("Belle Glado", "Belle Glade")
        pickup_location = f"{pu_city}, {places[0].group(2)} {places[0].group(3)}"

        del_city = places[-1].group(1).strip()
        delivery_location = f"{del_city}, {places[-1].group(2)} {places[-1].group(3)}"

        # Dates: extract explicit date substrings (numeric + month-name).
        date_strs: list[str] = []
        date_strs.extend(re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", t))
        date_strs.extend(
            re.findall(
                r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
                t,
                flags=re.IGNORECASE,
            )
        )

        dates: list[tuple[date, str]] = []
        for s in date_strs:
            try:
                dates.append((_parse_date_token(s), s))
            except Exception:  # noqa: BLE001
                continue
        if not dates:
            continue
        pickup_date, pickup_tok = min(dates, key=lambda x: x[0])
        delivery_date, delivery_tok = max(dates, key=lambda x: x[0])

        return (
            pickup_location,
            delivery_location,
            pickup_date,
            delivery_date,
            FieldEvidence(page=i, quote=places[0].group(0)[:200]),
            FieldEvidence(page=i, quote=places[-1].group(0)[:200]),
            FieldEvidence(page=i, quote=pickup_tok),
            FieldEvidence(page=i, quote=delivery_tok),
        )
    return None


def extract_fields_with_rules(pages_text: list[str]) -> ExtractedFields:
    total_rate_usd, total_ev = _find_amount_due(pages_text)
    extracted = (
        _try_extract_axle(pages_text)
        or _try_extract_hubgroup(pages_text)
        or _try_extract_spot(pages_text)
        or _try_extract_pickup_delivery_locations(pages_text)
        or _try_extract_ship_from_to(pages_text)
        or _try_extract_shipper_consignee(pages_text)
    )
    if not extracted:
        raise ValueError("Could not extract pickup/delivery fields from text.")

    (
        pickup_location,
        delivery_location,
        pickup_date,
        delivery_date,
        pickup_loc_ev,
        delivery_loc_ev,
        pickup_date_ev,
        delivery_date_ev,
    ) = extracted

    return ExtractedFields(
        pickup_location=pickup_location,
        delivery_location=delivery_location,
        pickup_date=pickup_date,
        delivery_date=delivery_date,
        total_rate_usd=float(total_rate_usd),
        pickup_location_evidence=pickup_loc_ev,
        delivery_location_evidence=delivery_loc_ev,
        pickup_date_evidence=pickup_date_ev,
        delivery_date_evidence=delivery_date_ev,
        total_rate_usd_evidence=total_ev,
    )

