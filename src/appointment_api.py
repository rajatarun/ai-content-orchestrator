import os
import json
import re
import boto3

ses = boto3.client("ses")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _cors_headers(event):
    origin = (event.get("headers") or {}).get("origin") or "*"
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "content-type,authorization,x-api-key,accept",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
    }


def _resp(event, code, body):
    h = {"Content-Type": "application/json"}
    h.update(_cors_headers(event))
    return {"statusCode": code, "headers": h, "body": json.dumps(body)}


def _json(event):
    b = event.get("body")
    if not b:
        return {}
    return json.loads(b) if isinstance(b, str) else b


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(event), "body": ""}

    if method != "POST" or path != "/public/appointment":
        return _resp(event, 404, {"ok": False})

    body = _json(event)

    name = body.get("name", "").strip()
    email = body.get("email", "").strip().lower()
    agenda = body.get("agenda", "").strip()
    preferred_time = body.get("preferredTime", "").strip()

    if not name or not email or not EMAIL_RE.match(email):
        return _resp(event, 400, {"ok": False, "error": "Invalid input"})

    subject = f"New Appointment Request from {name}"

    message_text = f"""
New Appointment Request

Name: {name}
Email: {email}
Preferred Time: {preferred_time}

Agenda:
{agenda}
"""

    ses.send_email(
        Source=os.environ["SES_FROM_EMAIL"],
        Destination={"ToAddresses": ["rajatarun12@gmail.com"]},
        Message={
            "Subject": {"Data": subject},
            "Body": {
                "Text": {"Data": message_text}
            },
        },
    )

    return _resp(event, 200, {"ok": True})