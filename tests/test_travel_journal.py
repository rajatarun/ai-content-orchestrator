"""The travel journal: admin uploads it with dates, the website reads it without.

What has to hold:

* the Ask Photos JSON is accepted as it comes (several years, one year, or a
  bare trip list), and a bad field is a 400 that names it, with nothing stored;
* uploads add trips and replace ones with the same id, so the journal can
  arrive in pieces;
* the public route carries no date in any form: no date fields, no photo
  timestamps, no trip ids (Ask Photos writes them as dates), no years in the
  text, and not in date order;
* only Instagram post links survive, since the website renders them as links.

The handlers run for real against an in-memory stand-in for the table.
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("ARTICLES_BUCKET", "test-bucket")

import admin_api  # noqa: E402
import db  # noqa: E402
import site_data  # noqa: E402
from travel_journal import parse_upload, public_key  # noqa: E402


class FakeBatch:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put_item(self, Item):
        self.table.put_item(Item=Item)


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)

    def batch_writer(self):
        return FakeBatch(self)

    def query(self, KeyConditionExpression, **_):
        # Only ever pk = "TRAVEL" here.
        pk = KeyConditionExpression.get_expression()["values"][1]
        return {"Items": [dict(v) for (p, _), v in sorted(self.items.items()) if p == pk]}


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(db, "_t", lambda: fake)
    monkeypatch.delenv("SITE_REBUILD_HOOK_URL", raising=False)
    return fake


def _call(module, method, path, body=None):
    event = {"httpMethod": method, "path": path, "headers": {"origin": "https://example.test"}}
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    resp = module.lambda_handler(event, None)
    return resp["statusCode"], json.loads(resp["body"] or "null")


def _trip(trip_id="2015-06-coast-weekend", **overrides):
    trip = {
        "id": trip_id,
        "title": "Coastal Weekend",
        "startDate": "2015-06-05",
        "endDate": "2015-06-07",
        "places": [
            {"city": None, "region": "Oregon", "country": "United States", "countryCode": "us", "lat": 45.0, "lng": -124.0}
        ],
        "tripType": "mixed",
        "summary": "The highlight was watching the 2015 kite festival fill the afternoon sky.",
        "highlights": ["Quiet harbor", "2015 kite festival"],
        "photos": [
            {
                "takenOn": "2015-06-06",
                "takenAt": "20:30",
                "city": None,
                "subject": "other",
                "description": "Fishing boats rest in a quiet harbor.",
                "orientation": "landscape",
                "isCover": True,
                "instagramUrl": "https://www.instagram.com/p/Example123/",
            },
            {"description": "Not a post", "isCover": False, "instagramUrl": "javascript:alert(1)"},
        ],
    }
    trip.update(overrides)
    return trip


# -- accepting the upload -----------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        {"years": [{"year": 2015, "trips": [_trip()]}]},
        {"year": 2015, "trips": [_trip()]},
        {"trips": [_trip()]},
    ],
)
def test_every_shape_ask_photos_returns_is_accepted(body):
    trips, error = parse_upload(body)
    assert error is None and [t["id"] for t in trips] == ["2015-06-coast-weekend"]
    assert trips[0]["places"][0]["countryCode"] == "US"


def test_only_instagram_post_links_are_kept():
    trips, _ = parse_upload({"trips": [_trip()]})
    assert [p["instagramUrl"] for p in trips[0]["photos"]] == ["https://www.instagram.com/p/Example123/", None]


@pytest.mark.parametrize(
    "body, where",
    [
        ({"years": [{"year": 2021, "trips": [_trip(startDate="21/03/2021")]}]}, "years.0.trips.0.startDate"),
        ({"trips": [_trip(places=[{"countryCode": "USA"}])]}, "trips.0.places.0.countryCode"),
        ({"trips": [_trip(places=[{"lat": 120}])]}, "trips.0.places.0.lat"),
        ({"trips": [_trip(trip_id="")]}, "trips.0.id"),
        ({"trips": []}, "no trips"),
        ({"articles": []}, "(root)"),
        ({"trips": [_trip(), _trip()]}, "appears twice"),
    ],
)
def test_a_bad_upload_names_the_field_and_stores_nothing(table, body, where):
    status, resp = _call(admin_api, "POST", "/admin/travel", body)
    assert status == 400 and where in resp["error"]
    assert table.items == {}


# -- storing it ---------------------------------------------------------------

def test_uploads_add_and_replace_by_id(table):
    status, resp = _call(admin_api, "POST", "/admin/travel", {"trips": [_trip()]})
    assert (status, resp["saved"], resp["total"], resp["rebuild"]) == (200, 1, 1, "not-configured")

    second = {"years": [{"year": 2015, "trips": [_trip(title="Renamed"), _trip("2016-02-river-town", startDate="2016-02-11")]}]}
    status, resp = _call(admin_api, "POST", "/admin/travel", second)
    assert (resp["saved"], resp["total"]) == (2, 2)

    status, full = _call(admin_api, "GET", "/admin/travel")
    assert full["count"] == 2
    assert [t["id"] for t in full["trips"]] == ["2015-06-coast-weekend", "2016-02-river-town"]
    assert full["trips"][0]["title"] == "Renamed"
    assert full["trips"][0]["startDate"] == "2015-06-05"  # admin keeps the dates

    status, resp = _call(admin_api, "DELETE", "/admin/travel/2016-02-river-town")
    assert status == 200 and resp["deleted"] == "2016-02-river-town"
    assert _call(admin_api, "GET", "/admin/travel")[1]["count"] == 1


# -- what the public sees -----------------------------------------------------

def test_the_public_journal_has_no_dates_in_any_form(table):
    _call(admin_api, "POST", "/admin/travel", {"trips": [_trip()]})
    status, public = _call(site_data, "GET", "/site/travel")
    assert status == 200
    trip = public["trips"][0]

    assert set(trip) == {"key", "title", "tripType", "summary", "highlights", "places", "posts"}
    assert trip["key"] == public_key("2015-06-coast-weekend") and "2015" not in trip["key"]
    assert trip["summary"] == "The highlight was watching the kite festival fill the afternoon sky."
    assert trip["highlights"] == ["Quiet harbor", "Kite festival"]
    assert trip["posts"] == [
        {"url": "https://www.instagram.com/p/Example123/", "description": "Fishing boats rest in a quiet harbor.", "isCover": True}
    ]
    assert set(trip["places"][0]) == {"city", "region", "countryCode", "lat", "lng"}

    text = json.dumps(public)
    assert not re.search(r"\b(19|20)\d{2}\b", text), text
    assert not re.search(r"January|February|March|April|June|July|August|September|October|November|December", text)
    assert not re.search(r"Date|takenOn|takenAt", text)


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("I walked the old town in December. The lights made it.", "I walked the old town. The lights made it."),
        ("I took a ferry on a windy March day.", "I took a ferry on a windy day."),
        ("I hiked the ridge in October, chasing the first snow.", "I hiked the ridge, chasing the first snow."),
        ("You may like it in May.", "You may like it in May."),
    ],
)
def test_months_are_taken_out_of_the_text(table, summary, expected):
    _call(admin_api, "POST", "/admin/travel", {"trips": [_trip(summary=summary)]})
    assert _call(site_data, "GET", "/site/travel")[1]["trips"][0]["summary"] == expected


def test_the_public_journal_is_not_in_date_order(table):
    trips = [
        _trip("2016-01-zz", title="Zebra crossing", startDate="2016-01-01"),
        _trip("2024-01-aa", title="Alpine lakes", startDate="2024-01-01"),
    ]
    _call(admin_api, "POST", "/admin/travel", {"trips": trips})
    titles = [t["title"] for t in _call(site_data, "GET", "/site/travel")[1]["trips"]]
    assert titles == ["Alpine lakes", "Zebra crossing"]


def test_nothing_uploaded_reads_as_an_empty_journal(table):
    assert _call(site_data, "GET", "/site/travel") == (200, {"trips": [], "dining": []})
