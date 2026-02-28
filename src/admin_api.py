import os
import json
import boto3
from decimal import Decimal
from urllib.parse import parse_qs

from logger import get_logger
from publisher import publish_article_to_s3
from db import (
    put_article, get_article, update_article, list_by_status,
    list_events, put_subscriber, list_subscribers, delete_subscriber
)

log = get_logger("admin_api")
lambda_client = boto3.client("lambda")

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
        "Access-Control-Allow-Headers": "content-type",
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
        status = (qs.get("status") or ["DRAFT"])[0]
        limit = int((qs.get("limit") or ["20"])[0])
        return _resp(event, 200, {"items": list_by_status(status, limit=limit)})

    if path.startswith("/admin/articles/"):
        parts = path.split("/")
        aid = parts[3] if len(parts) > 3 else None

        if aid and len(parts) == 4 and method == "GET":
            it = get_article(aid)
            return _resp(event, 200, it) if it else _resp(event, 404, {"error": "Not found"})

        if aid and len(parts) == 4 and method == "PATCH":
            body = _json(event)
            try:
                return _resp(event, 200, update_article(aid, body))
            except KeyError:
                return _resp(event, 404, {"error": "Not found"})

        if aid and len(parts) == 5 and parts[4] == "events" and method == "GET":
            return _resp(event, 200, {"items": list_events(aid)})

        if aid and len(parts) == 6 and parts[4] == "actions" and method == "POST":
            action = parts[5]
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
                updated = update_article(aid, {"status": "APPROVED"})
                try:
                    s3_info = publish_article_to_s3(updated)
                    add_event(aid, "PUBLISHED_TO_S3", f"Uploaded to s3://{s3_info['bucket']}/{s3_info['key']}")
                except Exception as e:
                    log.exception("s3_upload_failed", extra={"id": aid})
                    return _resp(event, 500, {"error": "S3 upload failed", "details": str(e)})
                return _resp(event, 200, {"ok": True, "article": updated, "s3": s3_info})

            if action == "request-edits":
                note = body.get("revisionNote", "")
                return _resp(event, 200, {"ok": True, "article": update_article(aid, {"status": "REVISION_REQUESTED", "revisionNote": note})})

            if action == "reject":
                reason = body.get("reason", "")
                return _resp(event, 200, {"ok": True, "article": update_article(aid, {"status": "REJECTED", "revisionNote": reason})})

            if action == "mark-published":
                return _resp(event, 200, {"ok": True, "article": update_article(aid, {
                    "status": "PUBLISHED",
                    "publishedAt": body.get("publishedAt"),
                    "publishedUrl": body.get("publishedUrl"),
                })})

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
        email = path.split("/admin/subscribers/", 1)[1]
        delete_subscriber(email)
        return _resp(event, 200, {"ok": True})

    return _resp(event, 404, {"error": "Route not found"})
