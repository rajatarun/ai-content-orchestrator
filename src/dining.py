"""Cafés and restaurants Tarun has been to, for the website's /traveller.

Uploaded from the admin UI (``POST /admin/dining``) as the JSON he exports
from his card transactions: ``{"places": [{"name", "category", "city",
"region", "visits"}]}`` (a bare list works too). An upload is a snapshot, so
it replaces the stored list.

A card statement is not a list of places, so before anything is shown:

* fast food goes: anything the export labels ``fast food``, and the chains it
  files under other headings (``FAST_FOOD_CHAINS``, Starbucks and Dunkin'
  included);
* so does anywhere in the home area (``HOME_AREA_TOWNS``): that is routine,
  not travel, and would map where he lives and works;
* so does everything that is not a venue: delivery apps, card offers,
  workplace cafeterias, generic words ("Cafe") and unreadable card codes;
* the same place spelt two ways ("Sugarloaf" / "Sugar loaf", "Moonbeam" /
  "Moonbeam Coffee") becomes one entry.

``GET /admin/dining`` shows what was kept and what was left out and why.
``GET /site/travel`` carries the kept list without ``visits``: how often
someone goes somewhere says where they live and work, which a public page has
no business saying.

Stored as one ContentTable item ``pk="DINING"``, ``sk="list"``, the upload as
a JSON string in ``data``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

DINING_KEY = {"pk": "DINING", "sk": "list"}
MAX_PLACES = 2000
CATEGORIES = ("cafe", "restaurant", "other dining", "fast food")

# Chains that are fast food whatever the export filed them under.
FAST_FOOD_CHAINS = {
    "arbys", "auntieannes", "bojangles", "braums", "burgerking", "carlsjr", "chickfila",
    "chipotle", "chipotlemexicangrill", "churchs", "dunkin", "dunkindonuts", "culvers", "dairyqueen", "deltaco", "dominos",
    "firehousesubs", "fiveguys", "hardees", "innout", "innoutburger", "jackinthebox",
    "jerseymikes", "jimmyjohns", "kfc", "littlecaesars", "mcdonalds", "pandaexpress",
    "papajohns", "pizzahut", "popeyes", "raisingcanes", "raisingcaneschickenfingers",
    "shakeshack", "sonic", "starbucks", "subway", "tacobell", "wendys", "whataburger", "whitecastle",
    "wingstop", "zaxbys",
}

# Not places: (pattern on the lowercased name, why it is left out).
NOT_A_PLACE = (
    (re.compile(r"doordash|^dd \*|uber ?eats|grubhub|postmates|instacart|too good to go"), "delivery or app, not a place"),
    (re.compile(r"^offer:"), "card offer, not a place"),
    (re.compile(r"\bjpmc\b|jp ?morgan"), "workplace cafeteria"),
    (re.compile(r"^(cafe|coffee|cafe coffee|tacos|restaurant|food)$"), "too generic to identify"),
    (re.compile(r"\d{5,}|^[a-z]{2,}\.[a-z]"), "unreadable card descriptor"),
)

# Home: the metro area where Tarun lives and works. The
# places he goes there are his routine, not travel, and a public list of them
# would map his week; so the whole metro area is left out.
HOME_AREA_TOWNS = {
    "addison", "allen", "anna", "arlington", "carrollton", "celina", "coppell", "dallas",
    "denton", "fairview", "farmersville", "flower mound", "fort worth", "frisco", "garland",
    "grand prairie", "grapevine", "irving", "lewisville", "little elm", "lucas", "mckinney",
    "melissa", "mesquite", "murphy", "plano", "princeton", "prosper", "richardson", "rockwall",
    "sachse", "southlake", "the colony", "wylie",
}

PUBLIC_FIELDS = ("name", "category", "city", "region")


class DiningError(ValueError):
    pass


def compact(name: str) -> str:
    """Letters and digits only, lowercased: how two spellings of one place compare."""
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("&", "and"))


def _text(value: Any, where: str, max_len: int = 120) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DiningError(f"{where}: must be text or null")
    value = " ".join(value.split())
    if len(value) > max_len:
        raise DiningError(f"{where}: longer than {max_len} characters")
    return value or None


def _entry(raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise DiningError(f"{where}: must be an object")
    name = _text(raw.get("name"), f"{where}.name")
    if not name:
        raise DiningError(f"{where}.name: required")
    category = (_text(raw.get("category"), f"{where}.category", 40) or "restaurant").lower()
    if category not in CATEGORIES:
        raise DiningError(f"{where}.category: one of {', '.join(CATEGORIES)}")
    visits = raw.get("visits", 1)
    if isinstance(visits, bool) or not isinstance(visits, int) or visits < 0:
        raise DiningError(f"{where}.visits: must be a whole number")
    if name.isupper() and len(name) > 3:
        name = name.title()  # "SAFFRON HOUSE" -> "Saffron House"
    return {
        "name": name,
        "category": category,
        "city": _text(raw.get("city"), f"{where}.city"),
        "region": _text(raw.get("region"), f"{where}.region", 60),
        "visits": visits,
    }


def parse_upload(body: Any) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Return ``(entries, None)`` for a storable upload, or ``(None, error)``."""
    try:
        if isinstance(body, dict):
            raw = body.get("places")
            where = "places"
        else:
            raw, where = body, "(root)"
        if not isinstance(raw, list) or not raw:
            raise DiningError(f'{where}: expected a non-empty list of places, as {{"places": [...]}}')
        if len(raw) > MAX_PLACES:
            raise DiningError(f"{where}: at most {MAX_PLACES} places")
        prefix = "places" if isinstance(body, dict) else ""
        entries = [_entry(item, f"{prefix}.{i}".lstrip(".")) for i, item in enumerate(raw)]
    except DiningError as error:
        return None, str(error)
    return entries, None


def _words(name: str) -> List[str]:
    return [w for w in (compact(part) for part in name.split()) if w]


def _same_place(a: str, b: str) -> bool:
    """Two spellings of one place, and not two places that share a first word.

    Same once spaces and punctuation go ("Sugar loaf" / "Sugarloaf"); one is
    the other's first words ("Moonbeam" / "Moonbeam Coffee"); or a card cut the name
    off mid-word ("Patelplaz" / "Patel Plaza"), which only counts when the
    cut runs past the first word, so "Madras" stays apart from "Madrasi
    Spice Kitchen".
    """
    if compact(a) == compact(b):
        return True
    short, long = sorted((a, b), key=lambda n: len(compact(n)))
    sw, lw = _words(short), _words(long)
    if len(sw) < len(lw) and lw[: len(sw)] == sw:
        return True
    return len(sw) == 1 and len(lw) > 1 and compact(long).startswith(sw[0]) and len(sw[0]) > len(lw[0])


def curate(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge spellings of one place, then keep what is a sit-down café or restaurant."""
    merged: List[Dict[str, Any]] = []
    for entry in entries:
        match = next((m for m in merged if any(_same_place(n, entry["name"]) for n in m["_names"])), None)
        if match is None:
            merged.append({**entry, "_names": [entry["name"]]})
            continue
        match["visits"] += entry["visits"]
        match["_names"].append(entry["name"])
        if entry["category"] == "fast food":
            match["category"] = "fast food"  # a cut-off card name of a fast-food place
        if not match["city"] and entry["city"]:
            match["city"], match["region"] = entry["city"], entry["region"]
        if len(entry["name"]) > len(match["name"]):
            match["name"] = entry["name"]

    kept, excluded = [], []
    for entry in merged:
        lowered = entry["name"].lower()
        reason = None
        if entry["category"] == "fast food":
            reason = "fast food"
        elif any(compact(n) in FAST_FOOD_CHAINS for n in entry["_names"]):
            reason = "fast food chain"
        elif (entry["city"] or "").strip().lower() in HOME_AREA_TOWNS:
            reason = "home area"
        else:
            reason = next((why for pattern, why in NOT_A_PLACE if pattern.search(lowered)), None)
        clean = {k: entry[k] for k in ("name", "category", "city", "region", "visits")}
        if len(entry["_names"]) > 1:
            clean["alsoListedAs"] = [n for n in entry["_names"] if n != entry["name"]]
        if reason:
            excluded.append({**clean, "reason": reason})
        else:
            kept.append(clean)
    return {"kept": kept, "excluded": excluded}


def public_dining(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The kept places, without visit counts, by city then name (never by how often)."""
    kept = curate(entries)["kept"]
    shown = [{field: entry.get(field) for field in PUBLIC_FIELDS} for entry in kept]
    shown.sort(key=lambda e: ((e["city"] or "~").lower(), e["name"].lower()))
    return shown


def admin_view(entries: List[Dict[str, Any]], updated_at: Optional[str] = None) -> Dict[str, Any]:
    curated = curate(entries)
    return {
        "uploaded": len(entries),
        "kept": curated["kept"],
        "excluded": curated["excluded"],
        "updatedAt": updated_at,
    }
