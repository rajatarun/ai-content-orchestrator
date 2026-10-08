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
from dining import curate, parse_upload  # noqa: E402


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
    assert all(set(d) == {"name", "category", "city", "region"} for d in public["dining"])
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
