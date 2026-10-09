"""Cafés and restaurants: a card export goes in, a public list of places comes out.

What has to hold:

* fast food is left out, whether the export labels it or files a known chain
  elsewhere (Braum's under "other dining", Starbucks and Dunkin' under "cafe");
* so is anywhere in the home area;
* so is everything that is not a place: delivery apps, card offers, workplace
  cafeterias, generic words and unreadable card codes;
* two spellings of one place are one entry, and two places sharing a first
  word are not ("Madras" / "Madrasi Spice Kitchen");
* the public list never carries visit counts, nor is it ordered by them;
* an upload replaces the list, and a bad one names the field and stores nothing.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("ARTICLES_BUCKET", "test-bucket")

import admin_api  # noqa: E402
import db  # noqa: E402
import site_data  # noqa: E402
from dining import curate, parse_notes, parse_reviews, parse_upload  # noqa: E402


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def query(self, **_):
        return {"Items": []}


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(db, "_t", lambda: fake)
    monkeypatch.delenv("SITE_REBUILD_HOOK_URL", raising=False)
    return fake


def _call(module, method, path, body=None):
    event = {"httpMethod": method, "path": path, "headers": {}}
    if body is not None:
        event["body"] = json.dumps(body)
    resp = module.lambda_handler(event, None)
    return resp["statusCode"], json.loads(resp["body"] or "null")


def place(name, category="restaurant", city=None, region=None, visits=1):
    return {"name": name, "category": category, "city": city, "region": region, "visits": visits}


# Made-up places, apart from the chains, whose names are the point.
EXPORT = {
    "description": "Cafes and restaurants visited, from card transactions.",
    "places": [
        place("Starbucks", "cafe", visits=9),
        place("Dunkin'", "cafe", visits=4),
        place("Braum's", "other dining", visits=3),
        place("Shake Shack", "fast food", visits=2),
        place("Patel Plaza", "fast food", visits=2),
        place("Patelplaz", "restaurant"),
        place("DoorDash", visits=3),
        place("DD *DOORDASH SOMEPLACE"),
        place("Offer:Dining: Example Perk", visits=2),
        place("JPMC Cafe", "cafe", visits=2),
        place("Cafe", "cafe"),
        place("XYZ STORE 9876543"),
        place("Lantern Noodle House", city="Plano", visits=6),
        place("Sugarloaf", "other dining", "Seattle"),
        place("Sugar loaf", "other dining", visits=2),
        place("Madras"),
        place("Madrasi Spice Kitchen"),
        place("1418 Coffeehouse", "cafe"),
        place("SAFFRON HOUSE", visits=2),
        place("Harbor Grill", city="Boston", region="MA"),
    ],
}


def test_fast_food_home_and_non_places_are_left_out_with_a_reason():
    entries, error = parse_upload(EXPORT)
    assert error is None
    curated = curate(entries)
    reasons = {e["name"]: e["reason"] for e in curated["excluded"]}
    assert reasons == {
        "Starbucks": "fast food chain",
        "Dunkin'": "fast food chain",
        "Braum's": "fast food chain",
        "Shake Shack": "fast food",
        "Patel Plaza": "fast food",
        "DoorDash": "delivery or app, not a place",
        "Dd *Doordash Someplace": "delivery or app, not a place",
        "Offer:Dining: Example Perk": "card offer, not a place",
        "JPMC Cafe": "workplace cafeteria",
        "Cafe": "too generic to identify",
        "Xyz Store 9876543": "unreadable card descriptor",
        "Lantern Noodle House": "home area",
    }
    kept = {e["name"] for e in curated["kept"]}
    assert kept == {
        "Sugar loaf", "Madras", "Madrasi Spice Kitchen", "1418 Coffeehouse", "Saffron House",
        "Harbor Grill",
    }


def test_two_spellings_merge_keeping_the_city_and_summing_visits():
    curated = curate(parse_upload(EXPORT)[0])
    sugar = next(e for e in curated["kept"] if e["name"] == "Sugar loaf")
    assert (sugar["city"], sugar["visits"], sugar["alsoListedAs"]) == ("Seattle", 3, ["Sugarloaf"])
    patel = next(e for e in curated["excluded"] if e["name"] == "Patel Plaza")
    assert patel["alsoListedAs"] == ["Patelplaz"]  # the cut-off card name joins the fast-food entry


def test_the_public_list_has_no_visit_counts_and_is_not_ranked(table):
    status, saved = _call(admin_api, "POST", "/admin/dining", EXPORT)
    assert status == 200 and saved["uploaded"] == 20 and saved["rebuild"] == "not-configured"

    status, public = _call(site_data, "GET", "/site/travel")
    assert status == 200
    names = [d["name"] for d in public["dining"]]
    # By city, then name; Saffron House's visits put it nowhere in particular.
    assert names == ["Harbor Grill", "Sugar loaf", "1418 Coffeehouse", "Madras",
                     "Madrasi Spice Kitchen", "Saffron House"]
    assert all(
        set(d) == {"name", "category", "city", "region", "rating", "review", "note", "score"} for d in public["dining"]
    )
    assert "visits" not in json.dumps(public)


def test_an_upload_replaces_the_list(table):
    _call(admin_api, "POST", "/admin/dining", EXPORT)
    _call(admin_api, "POST", "/admin/dining", [place("Harbor Grill", city="Boston")])
    status, view = _call(admin_api, "GET", "/admin/dining")
    assert [e["name"] for e in view["kept"]] == ["Harbor Grill"]
    assert view["kept"][0]["visits"] == 1  # admin still sees counts


@pytest.mark.parametrize(
    "body, where",
    [
        ({"places": []}, "places"),
        ({"places": [place("")]}, "places.0.name"),
        ({"places": [place("X", category="bar")]}, "places.0.category"),
        ({"places": [place("X", visits=-1)]}, "places.0.visits"),
        ("nope", "(root)"),
    ],
)
def test_a_bad_upload_names_the_field_and_stores_nothing(table, body, where):
    status, resp = _call(admin_api, "POST", "/admin/dining", body)
    assert status == 400 and where in resp["error"]
    assert table.items == {}


# -- reviews ------------------------------------------------------------------

REVIEWS_TEXT = """Coffee Shops, Bakeries & Cafés

Sugar Loaf — 5★
A bright little bakery. The cinnamon buns are worth the trip.

Starbucks — 4★
Reliable when I need to work.

Quick Service & Casual Dining

Moonbeam Coffee / Coffee Shops — 4★
Cold brew done right.

Harbor Grill — 5★
Fresh fish on the dock.

Ridge Bistro — 5★
The best patio near Plano.

Sit-Down Restaurants

The Olive Tree — 5★
A quiet terrace and excellent bread.
"""


def test_review_text_is_read_as_written():
    reviews, error = parse_reviews({"text": REVIEWS_TEXT})
    assert error is None
    assert [(r["name"], r["rating"], r["category"]) for r in reviews] == [
        ("Sugar Loaf", 5, "cafe"),
        ("Starbucks", 4, "cafe"),
        ("Moonbeam Coffee / Coffee Shops", 4, "cafe"),  # a mixed section: the name says café
        ("Harbor Grill", 5, "restaurant"),
        ("Ridge Bistro", 5, "restaurant"),
        ("The Olive Tree", 5, "restaurant"),
    ]
    assert reviews[0]["review"] == "A bright little bakery. The cinnamon buns are worth the trip."
    assert reviews[2]["names"] == ["Moonbeam Coffee"]  # "/ Coffee Shops" qualifies, it names nothing


@pytest.mark.parametrize(
    "body, where",
    [
        ({"text": "Just some words."}, "line 1"),
        ({"text": ""}, "no reviews"),
        ({"reviews": [{"name": "X", "rating": 6, "review": "ok"}]}, "reviews.0.rating"),
        ({"reviews": [{"name": "X", "rating": 4, "review": " "}]}, "reviews.0.review"),
        ({"stars": []}, "(root)"),
    ],
)
def test_bad_reviews_name_the_problem(body, where):
    reviews, error = parse_reviews(body)
    assert reviews is None and where in error


def test_reviews_land_on_their_places_and_never_lift_the_filters():
    reviews, _ = parse_reviews({"text": REVIEWS_TEXT})
    curated = curate(parse_upload(EXPORT)[0], reviews)
    kept = {e["name"]: e for e in curated["kept"]}

    assert kept["Sugar Loaf"]["rating"] == 5  # his spelling, on the card's "Sugar loaf"
    assert kept["Harbor Grill"]["review"] == "Fresh fish on the dock."
    assert kept["The Olive Tree"]["fromReview"] is True  # no card entry: a place of its own
    assert kept["Moonbeam Coffee"]["city"] is None

    report = {r["name"]: r for r in curated["reviews"]}
    assert report["Starbucks"]["public"] is False
    assert report["Starbucks"]["hiddenBecause"] == ["fast food chain"]
    assert report["Ridge Bistro"]["hiddenBecause"] == ["home area (named in the review)"]
    assert report["Sugar Loaf"]["matched"] == ["Sugar loaf"]


def test_saved_reviews_reach_the_public_list(table):
    _call(admin_api, "POST", "/admin/dining", EXPORT)
    status, saved = _call(admin_api, "POST", "/admin/dining/reviews", {"text": REVIEWS_TEXT})
    assert status == 200 and len(saved["reviews"]) == 6

    public = {d["name"]: d for d in _call(site_data, "GET", "/site/travel")[1]["dining"]}
    assert public["Harbor Grill"]["rating"] == 5
    assert public["Madras"]["rating"] is None
    assert "Starbucks" not in public and "Ridge Bistro" not in public

    # A new card upload keeps the reviews.
    _call(admin_api, "POST", "/admin/dining", EXPORT)
    assert {d["name"]: d for d in _call(site_data, "GET", "/site/travel")[1]["dining"]}["Harbor Grill"]["rating"] == 5


MORE_REVIEWS_TEXT = """Local DFW Restaurants & Cafés

Copper Kettle — 5★
Hand-pulled noodles, a short drive from home.

Gulf Shack — 5★
Right on the beach, which makes it part of the vacation.

Out-of-State Dining & Travel

Ridgeline Roasters — 5★
A specialty coffee stop I look for whenever I visit.

Sugar Loaf — 4★
Still good, if a little busier these days.
"""


def test_reviews_are_added_to_the_saved_ones(table):
    _call(admin_api, "POST", "/admin/dining", EXPORT)
    _call(admin_api, "POST", "/admin/dining/reviews", {"text": REVIEWS_TEXT})
    status, saved = _call(admin_api, "POST", "/admin/dining/reviews", {"text": MORE_REVIEWS_TEXT})
    assert status == 200

    report = {r["name"]: r for r in saved["reviews"]}
    assert len(report) == 9  # 6 + 4, Sugar Loaf reviewed again
    public = {d["name"]: d for d in _call(site_data, "GET", "/site/travel")[1]["dining"]}
    assert public["Sugar Loaf"]["rating"] == 4  # the newer review wins
    assert public["Harbor Grill"]["rating"] == 5  # the earlier list is kept
    assert public["Ridgeline Roasters"]["category"] == "cafe"
    assert public["Gulf Shack"]["review"].endswith("vacation.")  # away, though under "Local DFW"
    assert report["Copper Kettle"]["hiddenBecause"] == ["home area (listed as local)"]

    # "replace" swaps the set.
    _call(admin_api, "POST", "/admin/dining/reviews", {"text": MORE_REVIEWS_TEXT, "replace": True})
    public = {d["name"]: d for d in _call(site_data, "GET", "/site/travel")[1]["dining"]}
    assert public["Harbor Grill"]["rating"] is None


def test_a_review_names_dfw_or_shares_only_a_first_word_with_another_review():
    reviews, _ = parse_reviews({"text": """Coffee

Quiet Grounds — 5★
One of my favourite coffee spots in the DFW area.

Sit-Down Restaurants

Simply South — 5★
Dosas and filter coffee.

Simply Thai Bistro — 5★
Generous curries.
"""})
    report = {r["name"]: r for r in curate([], reviews)["reviews"]}
    assert report["Quiet Grounds"]["hiddenBecause"] == ["home area (named in the review)"]
    assert report["Simply South"]["public"] and report["Simply Thai Bistro"]["public"]
    assert report["Simply Thai Bistro"]["matched"] == []


def test_a_review_can_say_where_the_place_is():
    reviews, error = parse_reviews({"text": """Coffee

Rue Halles (Lyon) — 4★
Strong espresso.

Harbor Grill (Boston, MA) — 5★
Fresh fish.

Corner Cup (Plano, TX) — 5★
My usual.
"""})
    assert error is None
    assert [(r["name"], r["city"], r["region"]) for r in reviews] == [
        ("Rue Halles", "Lyon", None), ("Harbor Grill", "Boston", "MA"), ("Corner Cup", "Plano", "TX"),
    ]
    curated = curate([], reviews)
    kept = {e["name"]: e for e in curated["kept"]}
    assert kept["Rue Halles"]["city"] == "Lyon"
    report = {r["name"]: r for r in curated["reviews"]}
    assert report["Corner Cup"]["hiddenBecause"] == ["home area"]


# -- notes --------------------------------------------------------------------

NOTES_TEXT = """Local Spots Near Home (DFW)
 * Lantern Noodle House (100 Main St, Plano, TX 75024) – 4.6/5. Hand-pulled noodles in a small room.
 * Quiet Grounds (DFW area) – 4.5/5. A calm coffee bar.
 * Beach Shack (1 Shore Rd, Corpus Christi, TX 78401) – 4.3/5. Fish tacos by the water.
Out-of-State Dining (Travel)
 * Harbor Grill (5 Dock St, Boston, MA 02110) – 4.6/5. Fresh fish on the dock.
 * Sunrise Gelato (Asheville, NC) – 4.7/5. Homemade gelato and espresso.
 * Simply Lemongrass (Seattle, WA) – 4.5/5. Thai curries and noodle bowls.
 * Fast Food, Coffee, & Pizza: The list also includes Wendy's and Taco Bell.
(Note: Non-restaurant transactions were excluded.)
"""


def test_notes_are_read_as_listed_keeping_only_city_and_state():
    notes, error = parse_notes({"text": NOTES_TEXT})
    assert error is None
    assert [(n["name"], n["city"], n["region"], n["score"], n["homeArea"]) for n in notes] == [
        ("Lantern Noodle House", "Plano", "TX", 4.6, False),
        ("Quiet Grounds", None, None, 4.5, True),
        ("Beach Shack", "Corpus Christi", "TX", 4.3, False),  # a "local" section does not move a city
        ("Harbor Grill", "Boston", "MA", 4.6, False),
        ("Sunrise Gelato", "Asheville", "NC", 4.7, False),
        ("Simply Lemongrass", "Seattle", "WA", 4.5, False),
    ]
    assert notes[4]["category"] == "other dining" and notes[1]["category"] == "cafe"
    assert "Main St" not in json.dumps(notes)  # street addresses are not kept


def test_notes_attach_by_spelling_bring_cities_and_respect_the_filters():
    notes, _ = parse_notes({"text": NOTES_TEXT})
    curated = curate(parse_upload(EXPORT)[0], None, notes)
    kept = {e["name"]: e for e in curated["kept"]}
    report = {n["name"]: n for n in curated["notes"]}

    assert kept["Harbor Grill"]["note"] == "Fresh fish on the dock."  # on the card's Boston entry
    assert kept["Harbor Grill"]["score"] == 4.6
    assert kept["Beach Shack"]["fromNote"] is True
    assert report["Lantern Noodle House"]["hiddenBecause"] == ["home area"]
    assert report["Quiet Grounds"]["hiddenBecause"] == ["home area (listed as local)"]
    assert report["Simply Lemongrass"]["matched"] == []  # not the card's "Saffron House" or anything sharing a word


def test_a_note_gives_a_cityless_card_entry_its_city(table):
    _call(admin_api, "POST", "/admin/dining", [place("Ridge Bistro", visits=4)])
    status, saved = _call(admin_api, "POST", "/admin/dining/notes", {"notes": [
        {"name": "Ridge Bistro", "city": "Frisco", "region": "TX", "score": 4.2, "note": "Patio dining."},
    ]})
    assert status == 200
    assert saved["notes"][0]["hiddenBecause"] == ["home area"]  # caught now that it has a city
    assert "Ridge Bistro" not in {d["name"] for d in _call(site_data, "GET", "/site/travel")[1]["dining"]}


@pytest.mark.parametrize(
    "body, where",
    [
        ({"text": "Nothing that looks like a note."}, "no notes"),
        ({"notes": [{"name": "X", "score": 7}]}, "notes.0.score"),
        ({"notes": [{"name": ""}]}, "notes.0.name"),
        ({"places": []}, "(root)"),
    ],
)
def test_bad_notes_name_the_problem(body, where):
    notes, error = parse_notes(body)
    assert notes is None and where in error
