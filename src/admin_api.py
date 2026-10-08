import os
import json
import boto3
from decimal import Decimal
from urllib.parse import parse_qs, unquote

from logger import get_logger
from publisher import publish_article_to_s3
from db import (
    put_article, get_article, update_article, list_by_status,
    list_events, put_subscriber, list_subscribers, delete_subscriber, add_event,
    get_site_settings, put_site_settings,
    list_travel_trips, put_travel_trips, delete_travel_trip,
    get_dining, put_dining, get_dining_reviews, put_dining_reviews,
    get_dining_notes, put_dining_notes,
)
from site_settings import admin_view, caller_identity, validate_patch
import travel_journal
import dining
from statuses import (
    ALL_ARTICLE_STATUSES,
    APPROVED,
    ARCHIVED,
    AWAITING_APPROVAL,
    DRAFT,
    FAILED,
    PUBLISHED,
    REVISION_REQUESTED,
)

log = get_logger("admin_api")
lambda_client = boto3.client("lambda")

_ACTION_ALIASES = {
    "request-edits": "request-edits",
    "request-edit": "request-edits",
    "request_edits": "request-edits",
    "request_edit": "request-edits",
    "reject": "reject",
    "mark-failed": "mark-failed",
    "mark_failed": "mark-failed",
}


def _normalize_action(action):
    return _ACTION_ALIASES.get((action or "").strip().lower(), (action or "").strip().lower())


def _decorate_article_with_actions(article):
    if not isinstance(article, dict):
        return article

    status = (article.get("status") or "").upper()
    actions_by_status = {
        DRAFT: ["generate", "submit-for-approval", "request-edits", "reject"],
        REVISION_REQUESTED: ["submit-for-approval", "reject"],
        AWAITING_APPROVAL: ["approve", "request-edits", "reject"],
        APPROVED: ["mark-published", "archive"],
        PUBLISHED: ["archive"],
        FAILED: ["submit-for-approval", "archive"],
        ARCHIVED: [],
    }

    ctas = actions_by_status.get(status, [])
    article_with_actions = dict(article)
    article_with_actions["ctas"] = ctas
    article_with_actions["ctaPresentation"] = "dropdown" if len(ctas) > 2 else "inline"
    return article_with_actions

def _json_safe(obj):
    if isinstance(obj, list):
        return [_json_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj

def _cors_headers(event):
    hdrs = event.get("headers") or {}
    origin = hdrs.get("origin") or hdrs.get("Origin") or "*"
    allowed = os.environ.get("ALLOWED_ORIGIN", "*")
    allow_origin = origin if allowed == "*" or origin == allowed else allowed
    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Headers": "content-type,authorization,x-api-key,accept",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
        "Vary": "Origin",
    }

def _resp(event, code, body):
    h = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    h.update(_cors_headers(event))
    return {"statusCode": code, "headers": h, "body": json.dumps(_json_safe(body))}

def _json(event):
    b = event.get("body")
    if not b:
        return {}
    return json.loads(b) if isinstance(b, str) else b

def _qs(event):
    # REST API (v1)
    qsp = event.get("queryStringParameters") or {}
    if isinstance(qsp, dict) and qsp:
        out = {}
        for k, v in qsp.items():
            if v is None:
                continue
            out[k] = [str(v)]
        return out
    # HTTP API (v2)
    return parse_qs(event.get("rawQueryString") or "")

def _rebuild_site() -> str:
    """Ask the website to rebuild, so a journal change reaches /traveller.

    The site is a static export that reads GET /site/travel at build time.
    SITE_REBUILD_HOOK_URL is an Amplify incoming webhook; without one, the
    change shows on the site's next deploy. Never fails the request.
    """
    url = os.environ.get("SITE_REBUILD_HOOK_URL", "").strip()
    if not url:
        return "not-configured"
    import urllib.request
    try:
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return "triggered" if 200 <= resp.status < 300 else f"failed: HTTP {resp.status}"
    except Exception as e:  # noqa: BLE001 -- best effort by design
        log.warning("site_rebuild_failed", extra={"error": str(e)})
        return "failed"

def lambda_handler(event, context):
    path = event.get("path") or event.get("rawPath") or ""
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "").upper()

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(event), "body": ""}

    qs = _qs(event)
    log.info("request", extra={"path": path, "method": method, "qs": qs})

    # Health
    if path == "/admin" and method == "GET":
        return _resp(event, 200, {"ok": True})

    # Articles
    if path == "/admin/articles" and method == "POST":
        body = _json(event)
        if not body.get("title"):
            return _resp(event, 400, {"error": "title required"})
        return _resp(event, 201, put_article(body))

    if path == "/admin/articles" and method == "GET":
        status = (qs.get("status") or [DRAFT])[0].strip().upper()
        if status not in ALL_ARTICLE_STATUSES:
            return _resp(event, 400, {"error": f"invalid status '{status}'"})
        limit = int((qs.get("limit") or ["20"])[0])
        items = [_decorate_article_with_actions(item) for item in list_by_status(status, limit=limit)]
        return _resp(event, 200, {"items": items})

    if path.startswith("/admin/articles/"):
        parts = path.split("/")
        aid = parts[3] if len(parts) > 3 else None

        if aid and len(parts) == 4 and method == "GET":
            it = get_article(aid)
            return _resp(event, 200, _decorate_article_with_actions(it)) if it else _resp(event, 404, {"error": "Not found"})

        if aid and len(parts) == 4 and method == "PATCH":
            body = _json(event)
            try:
                updated = update_article(aid, body)
                return _resp(event, 200, _decorate_article_with_actions(updated))
            except KeyError:
                return _resp(event, 404, {"error": "Not found"})

        if aid and len(parts) == 5 and parts[4] == "events" and method == "GET":
            return _resp(event, 200, {"items": list_events(aid)})

        if aid and len(parts) == 6 and parts[4] == "actions" and method == "POST":
            action = _normalize_action(parts[5])
            body = _json(event)

            if action == "generate":
                fn = os.environ.get("GENERATE_FN_NAME", "")
                if not fn:
                    return _resp(event, 500, {"error": "GENERATE_FN_NAME not set"})
                lambda_client.invoke(
                    FunctionName=fn,
                    InvocationType="Event",
                    Payload=json.dumps({"articleId": aid}).encode("utf-8"),
                )
                return _resp(event, 202, {"ok": True})

            if action == "approve":
                updated = update_article(aid, {"status": APPROVED})
                try:
                    s3_info = publish_article_to_s3(updated)
                    add_event(aid, "PUBLISHED_TO_S3", f"Uploaded to s3://{s3_info['bucket']}/{s3_info['key']}")
                except Exception as e:
                    log.exception("s3_upload_failed", extra={"id": aid})
                    return _resp(event, 500, {"error": "S3 upload failed", "details": str(e)})
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated), "s3": s3_info})

            if action == "request-edits":
                note = body.get("revisionNote", "")
                updated = update_article(aid, {"status": REVISION_REQUESTED, "revisionNote": note})
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated)})

            if action == "reject":
                reason = body.get("reason", "")
                updated = update_article(aid, {"status": FAILED, "revisionNote": reason})
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated)})

            if action == "submit-for-approval":
                updated = update_article(aid, {"status": AWAITING_APPROVAL})
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated)})

            if action == "mark-failed":
                reason = body.get("reason", "")
                updated = update_article(aid, {"status": FAILED, "revisionNote": reason})
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated)})

            if action == "archive":
                reason = body.get("reason", "")
                patch = {"status": ARCHIVED}
                if reason:
                    patch["revisionNote"] = reason
                updated = update_article(aid, patch)
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated)})

            if action == "mark-published":
                updated = update_article(aid, {
                    "status": PUBLISHED,
                    "publishedAt": body.get("publishedAt"),
                    "publishedUrl": body.get("publishedUrl"),
                })
                return _resp(event, 200, {"ok": True, "article": _decorate_article_with_actions(updated)})

            return _resp(event, 404, {"error": "Unknown action"})

    # Newsletter
    if path == "/admin/newsletter/actions/generate" and method == "POST":
        fn = os.environ.get("NEWSLETTER_FN_NAME", "")
        if not fn:
            return _resp(event, 500, {"error": "NEWSLETTER_FN_NAME not set"})
        body = _json(event)
        payload = {"action": "generate"}
        payload.update(body)
        resp = lambda_client.invoke(FunctionName=fn, InvocationType="RequestResponse", Payload=json.dumps(payload).encode("utf-8"))
        data = json.loads(resp["Payload"].read().decode("utf-8"))
        return _resp(event, 200, data)

    if path == "/admin/newsletter/actions/send" and method == "POST":
        fn = os.environ.get("NEWSLETTER_FN_NAME", "")
        if not fn:
            return _resp(event, 500, {"error": "NEWSLETTER_FN_NAME not set"})
        body = _json(event)
        payload = {"action": "send"}
        payload.update(body)
        resp = lambda_client.invoke(FunctionName=fn, InvocationType="RequestResponse", Payload=json.dumps(payload).encode("utf-8"))
        data = json.loads(resp["Payload"].read().decode("utf-8"))
        return _resp(event, 200, data)

    # Site settings -- read by the resume website at runtime (site_settings.py)
    if path == "/admin/settings" and method == "GET":
        return _resp(event, 200, admin_view(get_site_settings()))

    if path == "/admin/settings" and method == "PATCH":
        try:
            body = _json(event)
        except ValueError:
            return _resp(event, 400, {"error": "body must be JSON"})
        patch, error = validate_patch(body)
        if error:
            return _resp(event, 400, {"error": error})
        return _resp(event, 200, admin_view(put_site_settings(patch, updated_by=caller_identity(event))))

    # Travel journal -- full history here; the website gets the undated view (travel_journal.py)
    if path == "/admin/travel" and method == "GET":
        return _resp(event, 200, travel_journal.admin_view(list_travel_trips()))

    if path == "/admin/travel" and method == "POST":
        try:
            body = _json(event)
        except ValueError:
            return _resp(event, 400, {"error": "body must be JSON"})
        trips, error = travel_journal.parse_upload(body)
        if error:
            return _resp(event, 400, {"error": error})
        saved = put_travel_trips(trips, updated_by=caller_identity(event))
        total = len(list_travel_trips())
        return _resp(event, 200, {"saved": saved, "total": total, "rebuild": _rebuild_site()})

    if path.startswith("/admin/travel/") and method == "DELETE":
        trip_id = unquote(path[len("/admin/travel/"):])
        if not travel_journal.TRIP_ID.match(trip_id):
            return _resp(event, 400, {"error": "invalid trip id"})
        delete_travel_trip(trip_id)
        return _resp(event, 200, {"deleted": trip_id, "rebuild": _rebuild_site()})

    # Cafés and restaurants -- the website shows the kept ones, without visit counts (dining.py)
    if path == "/admin/dining" and method == "GET":
        stored = get_dining()
        return _resp(event, 200, dining.admin_view(
            stored["entries"], stored["updatedAt"], get_dining_reviews(), get_dining_notes()))

    if path == "/admin/dining" and method == "POST":
        try:
            body = _json(event)
        except ValueError:
            return _resp(event, 400, {"error": "body must be JSON"})
        entries, error = dining.parse_upload(body)
        if error:
            return _resp(event, 400, {"error": error})
        updated_at = put_dining(entries, updated_by=caller_identity(event))
        view = dining.admin_view(entries, updated_at, get_dining_reviews(), get_dining_notes())
        return _resp(event, 200, {**view, "rebuild": _rebuild_site()})

    if path == "/admin/dining/reviews" and method == "POST":
        try:
            body = _json(event)
        except ValueError:
            return _resp(event, 400, {"error": "body must be JSON"})
        reviews, error = dining.parse_reviews(body)
        if error:
            return _resp(event, 400, {"error": error})
        put_dining_reviews(reviews, updated_by=caller_identity(event))
        stored = get_dining()
        view = dining.admin_view(stored["entries"], stored["updatedAt"], reviews, get_dining_notes())
        return _resp(event, 200, {**view, "rebuild": _rebuild_site()})

    if path == "/admin/dining/notes" and method == "POST":
        try:
            body = _json(event)
        except ValueError:
            return _resp(event, 400, {"error": "body must be JSON"})
        notes, error = dining.parse_notes(body)
        if error:
            return _resp(event, 400, {"error": error})
        put_dining_notes(notes, updated_by=caller_identity(event))
        stored = get_dining()
        view = dining.admin_view(stored["entries"], stored["updatedAt"], get_dining_reviews(), notes)
        return _resp(event, 200, {**view, "rebuild": _rebuild_site()})

    # Subscribers
    if path == "/admin/subscribers" and method == "GET":
        return _resp(event, 200, {"items": list_subscribers()})

    if path == "/admin/subscribers" and method == "POST":
        body = _json(event)
        email = (body.get("email") or "").strip()
        if not email:
            return _resp(event, 400, {"error": "email required"})
        return _resp(event, 201, put_subscriber(email))

    if path.startswith("/admin/subscribers/") and method == "DELETE":
        email = unquote(path.split("/admin/subscribers/", 1)[1])
        delete_subscriber(email)
        return _resp(event, 200, {"ok": True})

    return _resp(event, 404, {"error": "Route not found"})
