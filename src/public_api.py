import os
import json
import re
import boto3

from logger import get_logger
from db import put_subscriber

log = get_logger("public_api")

sesv2 = boto3.client("sesv2")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _cors_headers(event):
    hdrs = event.get("headers") or {}
    origin = hdrs.get("origin") or hdrs.get("Origin") or "*"
    allowed = os.environ.get("ALLOWED_ORIGIN", "*")
    allow_origin = origin if allowed == "*" or origin == allowed else allowed
    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Headers": "content-type,authorization,x-api-key,accept",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Vary": "Origin",
    }


def _resp(event, code, body):
    h = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    h.update(_cors_headers(event))
    return {"statusCode": code, "headers": h, "body": json.dumps(body, ensure_ascii=False, default=str)}


def _json(event):
    b = event.get("body")
    if not b:
        return {}
    return json.loads(b) if isinstance(b, str) else b


def lambda_handler(event, context):
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
    path = event.get("path") or event.get("rawPath") or ""

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(event), "body": ""}

    if method != "POST":
        return _resp(event, 405, {"ok": False, "error": "Method not allowed"})

    if path != "/public/subscribe":
        return _resp(event, 404, {"ok": False, "error": "Route not found"})

    body = _json(event)
    email = (body.get("email") or "").strip().lower()

    if not email or not EMAIL_RE.match(email):
        return _resp(event, 400, {"ok": False, "error": "Invalid email"})

    # 1) Store in DynamoDB (your existing subscriber store)
    try:
        put_subscriber(email)
    except Exception:
        log.exception("ddb_put_subscriber_failed", extra={"email": email})
        # Continue anyway — SES may still succeed

    # 2) Add to SES Contact List (SESv2)
    contact_list = os.environ.get("SES_CONTACT_LIST", "").strip()
    if not contact_list:
        # If you haven’t created a Contact List yet, still return ok for UI,
        # but tell you what to set up.
        return _resp(event, 200, {
            "ok": True,
            "email": email,
            "ses": {"ok": False, "error": "SES_CONTACT_LIST not set (create a contact list in SESv2)"},
        })

    try:
        sesv2.create_contact(
            ContactListName=contact_list,
            EmailAddress=email,
            TopicPreferences=[],
            UnsubscribeAll=False
        )
        return _resp(event, 200, {"ok": True, "email": email, "ses": {"ok": True}})
    except sesv2.exceptions.AlreadyExistsException:
        return _resp(event, 200, {"ok": True, "email": email, "ses": {"ok": True, "already": True}})
    except Exception as e:
        log.exception("ses_create_contact_failed", extra={"email": email})
        return _resp(event, 200, {"ok": True, "email": email, "ses": {"ok": False, "error": str(e)}})