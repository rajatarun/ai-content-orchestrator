"""Site settings: the admin UI writes them, the website reads them on every load.

The website is a static export, so this pair of routes is the only way a
change made in the admin UI reaches it without a rebuild. What has to hold:

* a save validates before it writes -- an unknown field or a malformed design
  name is a 400, never a silent no-op or a stored value the site cannot use;
* what a save wrote is exactly what the public route then returns;
* the public route never leaks admin-only fields (who saved it);
* before anything has ever been saved, the public route still answers 200 with
  every field present and null, which the website reads as "use your default".

The handlers run for real against an in-memory stand-in for the one DynamoDB
table call they make, so none of this needs AWS.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# The handler modules build boto3 clients at import time, which botocore
# refuses to do without a region. Nothing here calls AWS.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("ARTICLES_BUCKET", "test-bucket")

import admin_api  # noqa: E402
import db  # noqa: E402
import site_data  # noqa: E402
from site_settings import validate_patch  # noqa: E402


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(db, "_t", lambda: fake)
    return fake


def _call(module, method, path, body=None, principal=None):
    event = {"httpMethod": method, "path": path, "headers": {"origin": "https://example.test"}}
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    if principal:
        event["requestContext"] = {"authorizer": {"principalId": principal}}
    resp = module.lambda_handler(event, None)
    return resp["statusCode"], json.loads(resp["body"] or "null")


# -- validation ---------------------------------------------------------------

@pytest.mark.parametrize("name", ["terracotta", "midnight", "v2-dark", "a"])
def test_design_names_are_accepted(name):
    assert validate_patch({"homeVariant": name}) == ({"homeVariant": name}, None)


def test_design_names_are_normalised():
    assert validate_patch({"homeVariant": "  Terracotta "}) == ({"homeVariant": "terracotta"}, None)


@pytest.mark.parametrize("value", ["", "2fast", "has space", "x" * 33, "<script>", 7, None])
def test_malformed_design_names_are_refused(value):
    patch, error = validate_patch({"homeVariant": value})
    assert patch is None and "homeVariant" in error


@pytest.mark.parametrize("body", [None, {}, [], "terracotta"])
def test_a_body_without_settings_is_refused(body):
    patch, error = validate_patch(body)
    assert patch is None and error


def test_unknown_fields_are_refused_not_ignored():
    patch, error = validate_patch({"homeVariant": "midnight", "homeVarient": "x"})
    assert patch is None and "homeVarient" in error


# -- the round trip -----------------------------------------------------------

def test_never_saved_reads_as_all_null(table):
    status, body = _call(site_data, "GET", "/site/settings")
    assert status == 200
    assert body == {"homeVariant": None, "updatedAt": None}


def test_what_admin_saves_is_what_the_site_reads(table):
    status, saved = _call(admin_api, "PATCH", "/admin/settings", {"homeVariant": "midnight"}, principal="0xabc")
    assert status == 200
    assert saved["homeVariant"] == "midnight"
    assert saved["updatedBy"] == "0xabc"
    assert saved["updatedAt"]

    status, public = _call(site_data, "GET", "/site/settings")
    assert status == 200
    assert public == {"homeVariant": "midnight", "updatedAt": saved["updatedAt"]}


def test_the_public_route_does_not_expose_who_saved(table):
    _call(admin_api, "PATCH", "/admin/settings", {"homeVariant": "terracotta"}, principal="0xabc")
    _, public = _call(site_data, "GET", "/site/settings")
    assert "updatedBy" not in public


def test_admin_get_returns_the_saved_settings(table):
    _call(admin_api, "PATCH", "/admin/settings", {"homeVariant": "terracotta"}, principal="0xabc")
    status, body = _call(admin_api, "GET", "/admin/settings")
    assert status == 200
    assert body["homeVariant"] == "terracotta" and body["updatedBy"] == "0xabc"


def test_a_refused_save_writes_nothing(table):
    _call(admin_api, "PATCH", "/admin/settings", {"homeVariant": "midnight"})
    status, body = _call(admin_api, "PATCH", "/admin/settings", {"homeVariant": "Not A Name!"})
    assert status == 400 and "homeVariant" in body["error"]
    _, public = _call(site_data, "GET", "/site/settings")
    assert public["homeVariant"] == "midnight"


def test_a_body_that_is_not_json_is_a_400(table):
    status, body = _call(admin_api, "PATCH", "/admin/settings", "{not json")
    assert status == 400 and body["error"]


def test_the_public_route_answers_cors_preflight(table):
    resp = site_data.lambda_handler({"httpMethod": "OPTIONS", "path": "/site/settings", "headers": {}}, None)
    assert resp["statusCode"] == 200
    assert "GET" in resp["headers"]["Access-Control-Allow-Methods"]
