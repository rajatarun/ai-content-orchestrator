"""Tarun's travel journal: stored here, shown on the resume website's /traveller.

The journal comes from Google Photos' Ask Photos (the prompt lives with the
website), as JSON: one year ``{"year": 2019, "trips": [...]}``, several
``{"years": [...]}``, or a bare ``{"trips": [...]}``. The admin UI uploads it
through ``POST /admin/travel``; each trip is stored under its own id, so a
second upload adds trips and replaces ones it repeats, and the journal can
arrive in pieces.

Two views, and the difference between them is the point:

* ``GET /admin/travel`` -- everything, dates included. Admin only.
* ``GET /site/travel`` -- what anyone may see: trips with their places,
  summaries, highlights and Instagram links, and **no dates**. Not the date
  fields, not photo timestamps, not the trip ids (which Ask Photos writes as
  ``2015-06-coast-weekend``), not years written into the text, and not in date order.
  The website builds /traveller from this route alone.

Validation names the field that is wrong (``years.0.trips.3.startDate``) so a
bad paste is fixable from the error. Ask Photos answers null when unsure, so
almost everything may be null; what may not be is a trip without an id.

Stored as ContentTable items ``pk="TRAVEL"``, ``sk=<trip id>``, the trip as a
JSON string in ``data`` (DynamoDB would otherwise demand Decimal coordinates).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

TRAVEL_PK = "TRAVEL"

TRIP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")
# A public Instagram post or reel. Anything else (another site, a javascript:
# URL) is dropped, since the website renders these as links.
INSTAGRAM_POST = re.compile(r"^https://(www\.)?instagram\.com/(p|reel)/[A-Za-z0-9_-]+/?$")
# A year written into the text: "the 2015 kite festival".
YEAR_IN_TEXT = re.compile(r"\s*\b(?:19|20)\d{2}\b")
# Month names ("May" left out: it is far more often the verb). In order: a
# phrase that only dates ("in December"), a month used as an adjective ("a
# windy March day"), then any month left.
_MONTH = r"(?:January|February|March|April|June|July|August|September|October|November|December)"
MONTH_PHRASE = re.compile(rf"\s*\b(?:in|during|throughout|this|last|early|late|mid)[- ]{_MONTH}\b", re.I)
MONTH_BEFORE_WORD = re.compile(rf"\b{_MONTH}\s+(?=[a-z])")
MONTH_ANYWHERE = re.compile(rf"\s*\b{_MONTH}\b")

MAX_TRIPS_PER_UPLOAD = 500


class JournalError(ValueError):
    """An upload that cannot be stored, with the path of the offending field."""


def _text(value: Any, where: str, max_len: int = 2000) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise JournalError(f"{where}: must be text or null")
    value = value.strip()
    if len(value) > max_len:
        raise JournalError(f"{where}: longer than {max_len} characters")
    return value or None


def _number(value: Any, where: str, low: float, high: float) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise JournalError(f"{where}: must be a number from {low} to {high}, or null")
    return float(value)


def _date(value: Any, where: str) -> Optional[str]:
    value = _text(value, where, 10)
    if value is not None and not ISO_DATE.match(value):
        raise JournalError(f"{where}: dates must be YYYY-MM-DD")
    return value


def _place(raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise JournalError(f"{where}: must be an object")
    code = _text(raw.get("countryCode"), f"{where}.countryCode", 2)
    if code is not None and not COUNTRY_CODE.match(code):
        raise JournalError(f"{where}.countryCode: must be ISO 3166 alpha-2, like US")
    return {
        "city": _text(raw.get("city"), f"{where}.city", 120),
        "region": _text(raw.get("region"), f"{where}.region", 120),
        "country": _text(raw.get("country"), f"{where}.country", 120),
        "countryCode": code.upper() if code else None,
        "lat": _number(raw.get("lat"), f"{where}.lat", -90, 90),
        "lng": _number(raw.get("lng"), f"{where}.lng", -180, 180),
    }


def _photo(raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise JournalError(f"{where}: must be an object")
    url = raw.get("instagramUrl")
    return {
        "takenOn": _date(raw.get("takenOn"), f"{where}.takenOn"),
        "takenAt": _text(raw.get("takenAt"), f"{where}.takenAt", 8),
        "city": _text(raw.get("city"), f"{where}.city", 120),
        "subject": _text(raw.get("subject"), f"{where}.subject", 40),
        "description": _text(raw.get("description"), f"{where}.description", 500),
        "orientation": _text(raw.get("orientation"), f"{where}.orientation", 20),
        "isCover": raw.get("isCover") is True,
        # Kept only when it is an Instagram post; a bad link is dropped, not an error.
        "instagramUrl": url if isinstance(url, str) and INSTAGRAM_POST.match(url) else None,
    }


def _list(value: Any, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise JournalError(f"{where}: must be a list")
    return value


def _trip(raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise JournalError(f"{where}: must be an object")
    trip_id = raw.get("id")
    if not isinstance(trip_id, str) or not TRIP_ID.match(trip_id.strip()):
        raise JournalError(f"{where}.id: required; letters, digits, '.', '_' and '-' only")
    highlights = [
        _text(h, f"{where}.highlights.{i}", 200) for i, h in enumerate(_list(raw.get("highlights"), f"{where}.highlights"))
    ]
    return {
        "id": trip_id.strip(),
        "title": _text(raw.get("title"), f"{where}.title", 200),
        "startDate": _date(raw.get("startDate"), f"{where}.startDate"),
        "endDate": _date(raw.get("endDate"), f"{where}.endDate"),
        "tripType": _text(raw.get("tripType"), f"{where}.tripType", 40),
        "summary": _text(raw.get("summary"), f"{where}.summary"),
        "highlights": [h for h in highlights if h],
        "places": [_place(p, f"{where}.places.{i}") for i, p in enumerate(_list(raw.get("places"), f"{where}.places"))],
        "photos": [_photo(p, f"{where}.photos.{i}") for i, p in enumerate(_list(raw.get("photos"), f"{where}.photos"))],
    }


def parse_upload(body: Any) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Return ``(trips, None)`` for a storable upload, or ``(None, error)``."""
    try:
        if not isinstance(body, dict):
            raise JournalError("(root): must be a JSON object")
        if "years" in body:
            located = []
            for y, year in enumerate(_list(body["years"], "years")):
                if not isinstance(year, dict):
                    raise JournalError(f"years.{y}: must be an object")
                located += [(t, f"years.{y}.trips.{i}") for i, t in enumerate(_list(year.get("trips"), f"years.{y}.trips"))]
        elif "trips" in body:
            located = [(t, f"trips.{i}") for i, t in enumerate(_list(body["trips"], "trips"))]
        else:
            raise JournalError('(root): expected {"years": [...]}, {"year": ..., "trips": [...]} or {"trips": [...]}')
        if not located:
            raise JournalError("(root): no trips in the upload")
        if len(located) > MAX_TRIPS_PER_UPLOAD:
            raise JournalError(f"(root): at most {MAX_TRIPS_PER_UPLOAD} trips per upload")
        trips = [_trip(raw, where) for raw, where in located]
    except JournalError as error:
        return None, str(error)

    seen = set()
    for trip in trips:
        if trip["id"] in seen:
            return None, f"trip id {trip['id']} appears twice in the upload"
        seen.add(trip["id"])
    return trips, None


def _undated(text: Optional[str]) -> Optional[str]:
    """Text with years and months taken out.

    "the 2015 kite festival" -> "the kite festival"; "the old town in
    December." -> "the old town."; "a windy March day" -> "a windy day".
    """
    if not text:
        return text
    out = YEAR_IN_TEXT.sub("", text)
    out = MONTH_PHRASE.sub("", out)
    out = MONTH_BEFORE_WORD.sub("", out)
    out = MONTH_ANYWHERE.sub("", out).strip()
    if out and text[0].isdigit():
        out = out[0].upper() + out[1:]  # "2015 kite festival" -> "Kite festival"
    return out or None


def public_key(trip_id: str) -> str:
    """A stable, opaque handle for a trip: its id says when it was, this does not."""
    return hashlib.sha256(f"travel:{trip_id}".encode("utf-8")).hexdigest()[:12]


def public_trip(trip: Dict[str, Any]) -> Dict[str, Any]:
    """One trip as anyone may see it. No dates in any form."""
    posts = [
        {"url": p["instagramUrl"], "description": _undated(p.get("description")), "isCover": bool(p.get("isCover"))}
        for p in trip.get("photos") or []
        if p.get("instagramUrl")
    ]
    posts.sort(key=lambda post: not post["isCover"])
    return {
        "key": public_key(trip["id"]),
        "title": _undated(trip.get("title")),
        "tripType": trip.get("tripType"),
        "summary": _undated(trip.get("summary")),
        "highlights": [h for h in (_undated(h) for h in trip.get("highlights") or []) if h],
        "places": [
            {k: place.get(k) for k in ("city", "region", "countryCode", "lat", "lng")}
            for place in trip.get("places") or []
        ],
        "posts": posts,
    }


def public_view(trips: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """GET /site/travel. Ordered by country, then title: never by when."""
    shown = [public_trip(trip) for trip in trips]
    shown.sort(
        key=lambda t: (
            next((p["countryCode"] for p in t["places"] if p.get("countryCode")), "~"),
            (t["title"] or "").lower(),
            t["key"],
        )
    )
    return {"trips": shown}


def admin_view(trips: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """GET /admin/travel. Everything, oldest first."""
    ordered = sorted(trips, key=lambda t: (t.get("startDate") or "", t["id"]))
    return {"trips": ordered, "count": len(ordered)}
