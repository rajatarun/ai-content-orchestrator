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

Reviews (``POST /admin/dining/reviews``, the text as written: "Name — 4★"
and a paragraph) attach to the places they name, or add places of their own.
They never lift a place past the filters above.

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
REVIEWS_KEY = {"pk": "DINING", "sk": "reviews"}
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

PUBLIC_FIELDS = ("name", "category", "city", "region", "rating", "review")


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


def _review_key(name: str) -> str:
    """How a review's name compares to a card name: "85°C" is "85 Degrees C", "The X" is "X"."""
    name = name.replace("°", " degrees ").replace("+", " and ")
    name = re.sub(r"^the\s+", "", name.strip(), flags=re.I)
    return name


def _review_matches(review_name: str, entry_name: str) -> bool:
    """A review names this card entry: same place by the merge rules, or the same
    distinctive first word ("Armor Coffee" / "Armor Company", "Earls Kitchen + Bar" /
    "Earls Legacy")."""
    a, b = _review_key(review_name), _review_key(entry_name)
    if _same_place(a, b):
        return True
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and wa[0] == wb[0] and len(wa[0]) >= 5 and wa[0] not in GENERIC_FIRST_WORDS


def curate(entries: List[Dict[str, Any]], reviews: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Merge spellings of one place, attach the reviews, then keep what is a
    sit-down café or restaurant away from home.

    A review lands on the card entry it names; one that names no entry becomes
    a place of its own (no city: the review does not say). Reviews never lift
    a place past the filters: a reviewed fast-food or home-area place stays
    off the public page, and the admin view says so.
    """
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

    review_report = []
    for review in reviews or []:
        landed = []
        for part in review["names"]:
            targets = [m for m in merged if any(_review_matches(part, n) for n in m["_names"])]
            if not targets:
                targets = [{
                    "name": part, "category": review["category"], "city": None, "region": None,
                    "visits": 0, "_names": [part], "_fromReview": True,
                }]
                merged.append(targets[0])
            for target in targets:
                target["rating"], target["review"] = review["rating"], review["review"]
                target.setdefault("_cardName", target["name"])
                target["name"] = part  # his spelling over the card's ("Armor Coffee", not "Armor Company")
                landed.append(target)
        review_report.append({"review": review, "landed": landed})

    kept, excluded = [], []
    for entry in merged:
        lowered = entry["name"].lower()
        review_text = (entry.get("review") or "").lower()
        reason = None
        if entry["category"] == "fast food":
            reason = "fast food"
        elif any(compact(n) in FAST_FOOD_CHAINS for n in entry["_names"]):
            reason = "fast food chain"
        elif (entry["city"] or "").strip().lower() in HOME_AREA_TOWNS:
            reason = "home area"
        elif any(re.search(rf"\b{re.escape(town)}\b", review_text) for town in HOME_AREA_TOWNS):
            reason = "home area (named in the review)"
        else:
            reason = next((why for pattern, why in NOT_A_PLACE if pattern.search(lowered)), None)
        entry["_reason"] = reason
        clean = {k: entry[k] for k in ("name", "category", "city", "region", "visits")}
        if entry.get("rating"):
            clean["rating"], clean["review"] = entry["rating"], entry["review"]
        if entry.get("_fromReview"):
            clean["fromReview"] = True
        if len(entry["_names"]) > 1:
            clean["alsoListedAs"] = [n for n in entry["_names"] if n != entry["name"]]
        if reason:
            excluded.append({**clean, "reason": reason})
        else:
            kept.append(clean)

    reviewed = [
        {
            "name": item["review"]["name"],
            "rating": item["review"]["rating"],
            "matched": [t["_cardName"] for t in item["landed"] if not t.get("_fromReview")],
            "public": any(not t["_reason"] for t in item["landed"]),
            "hiddenBecause": sorted({t["_reason"] for t in item["landed"] if t["_reason"]}),
        }
        for item in review_report
    ]
    return {"kept": kept, "excluded": excluded, "reviews": reviewed}


def public_dining(entries: List[Dict[str, Any]], reviews: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The kept places, without visit counts, by city then name (never by how often)."""
    kept = curate(entries, reviews)["kept"]
    shown = [{field: entry.get(field) for field in PUBLIC_FIELDS} for entry in kept]
    shown.sort(key=lambda e: ((e["city"] or "~").lower(), e["name"].lower()))
    return shown


def admin_view(
    entries: List[Dict[str, Any]],
    updated_at: Optional[str] = None,
    reviews: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    curated = curate(entries, reviews)
    return {
        "uploaded": len(entries),
        "kept": curated["kept"],
        "excluded": curated["excluded"],
        "reviews": curated["reviews"],
        "updatedAt": updated_at,
    }


# -- reviews ------------------------------------------------------------------

REVIEW_HEADER = re.compile(r"^(?P<name>.+?)\s+[—–-]\s*(?P<rating>[1-5])\s*(?:★|stars?\b|/\s*5)\s*$", re.I)
GENERIC_FIRST_WORDS = {"the", "cafe", "coffee", "le", "la", "les", "el", "pizza", "taco", "tacos"}
# Parts of a review title that qualify it rather than name a place ("Domino's / Pizza Places").
NOT_A_NAME = re.compile(r"^(pizza|coffee|burger|taco)?\s*(places|shops|spots|chains?)$", re.I)
MAX_REVIEW = 2000


def _section_category(section: Optional[str]) -> str:
    s = (section or "").lower()
    if "coffee" in s or "café" in s or "cafe" in s or "bakeries" in s:
        return "cafe"
    return "restaurant"


def _review(raw: Dict[str, Any], where: str) -> Dict[str, Any]:
    name = _text(raw.get("name"), f"{where}.name")
    if not name:
        raise DiningError(f"{where}.name: required")
    rating = raw.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise DiningError(f"{where}.rating: a whole number from 1 to 5")
    text = raw.get("review")
    if not isinstance(text, str) or not text.strip():
        raise DiningError(f"{where}.review: required")
    text = " ".join(text.split())
    if len(text) > MAX_REVIEW:
        raise DiningError(f"{where}.review: longer than {MAX_REVIEW} characters")
    section = _text(raw.get("section"), f"{where}.section", 80)
    names = [part.strip() for part in name.split("/") if part.strip() and not NOT_A_NAME.match(part.strip())]
    if not names:
        raise DiningError(f"{where}.name: no place name in it")
    return {
        "name": name, "names": names, "rating": rating, "review": text,
        "section": section, "category": _section_category(section),
    }


def parse_reviews_text(text: str) -> List[Dict[str, Any]]:
    """Reviews as written: an optional section heading, then "Name — 4★" and a paragraph.

    A line is a section heading when the next non-blank line is a review title.
    Everything else after a title, up to the next title or heading, is that
    review's text.
    """
    lines = [line.strip() for line in text.splitlines()]
    out: List[Dict[str, Any]] = []
    section: Optional[str] = None
    current: Optional[Dict[str, Any]] = None
    for i, line in enumerate(lines):
        if not line:
            continue
        header = REVIEW_HEADER.match(line)
        if header:
            current = {"name": header["name"], "rating": int(header["rating"]), "review": "", "section": section}
            out.append(current)
            continue
        following = next((l for l in lines[i + 1:] if l), "")
        if REVIEW_HEADER.match(following) and not line.endswith((".", "!", "?")):
            section, current = line, None
            continue
        if current is None:
            raise DiningError(f'line {i + 1}: text before the first "Name — 4★" title')
        current["review"] = f"{current['review']} {line}".strip()
    return out


def parse_reviews(body: Any) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """``{"text": "..."}`` as written, or ``{"reviews": [{name, rating, review, section}]}``."""
    try:
        if isinstance(body, dict) and isinstance(body.get("text"), str):
            raw = parse_reviews_text(body["text"])
            where = "text"
        elif isinstance(body, dict) and isinstance(body.get("reviews"), list):
            raw, where = body["reviews"], "reviews"
        else:
            raise DiningError('(root): expected {"text": "..."} or {"reviews": [...]}')
        if not raw:
            raise DiningError(f"{where}: no reviews found")
        if len(raw) > MAX_PLACES:
            raise DiningError(f"{where}: at most {MAX_PLACES} reviews")
        reviews = [_review(r if isinstance(r, dict) else {}, f"{where}.{i}") for i, r in enumerate(raw)]
    except DiningError as error:
        return None, str(error)
    return reviews, None
