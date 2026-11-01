"""Upload documents and photos into ContextWeave finance/health S3 buckets.

Objects land under ``uploads/<year>/<month>/<uuid>-<filename>`` so EventBridge
ingest on those buckets can pick them up. Only plain docs buckets — not -mm
multimodal buckets.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import uuid
from datetime import datetime, timezone

# Decoded payload limit. API Gateway allows ~10 MiB total; base64 + JSON overhead
# means the binary body must stay below that.
MAX_DECODED_BYTES = 8 * 1024 * 1024

ALLOWED_CONTENT_TYPES = frozenset({
    "application/pdf",
    "text/plain",
    "text/markdown",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
})

_CONTENT_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
    "text/x-markdown": "text/markdown",
}

_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_BUCKET_ENV = {
    "finance": "CONTEXTWEAVE_FINANCE_BUCKET",
    "health": "CONTEXTWEAVE_HEALTH_BUCKET",
}


def normalize_content_type(content_type: str) -> str | None:
    ct = (content_type or "").strip().lower().split(";", 1)[0].strip()
    if not ct:
        return None
    return _CONTENT_TYPE_ALIASES.get(ct, ct)


def safe_filename(name: str) -> str | None:
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        return None
    base = raw.replace("\\", "/").split("/")[-1]
    if not base or base in (".", ".."):
        return None
    cleaned = _FILENAME_RE.sub("_", base).strip("._")
    if not cleaned or len(cleaned) > 200:
        return None
    return cleaned


def build_object_key(filename: str, now: datetime | None = None) -> str:
    ts = now or datetime.now(timezone.utc)
    return f"uploads/{ts:%Y/%m}/{uuid.uuid4()}-{filename}"


def parse_upload(body: dict) -> tuple[bytes, str, str, str | None]:
    """Validate JSON upload body. Returns (bytes, content_type, filename, error)."""
    if not isinstance(body, dict):
        return b"", "", "", "body must be a JSON object"

    filename = safe_filename(str(body.get("filename") or ""))
    if not filename:
        return b"", "", "", "filename required (non-empty, no path segments)"

    content_type = normalize_content_type(str(body.get("contentType") or ""))
    if not content_type:
        return b"", "", "", "contentType required"
    if content_type not in ALLOWED_CONTENT_TYPES:
        return b"", "", "", f"contentType not allowed: {content_type}"

    raw_b64 = body.get("contentBase64")
    if raw_b64 is None or raw_b64 == "":
        return b"", "", "", "contentBase64 required"
    if not isinstance(raw_b64, str):
        return b"", "", "", "contentBase64 must be a string"

    try:
        data = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        return b"", "", "", "contentBase64 is not valid base64"

    if not data:
        return b"", "", "", "contentBase64 decodes to empty payload"
    if len(data) > MAX_DECODED_BYTES:
        return b"", "", "", f"payload exceeds {MAX_DECODED_BYTES} bytes"

    return data, content_type, filename, None


def bucket_for_domain(domain: str) -> tuple[str | None, str | None]:
    """Map finance|health to bucket name from env. Returns (bucket, error)."""
    key = (domain or "").strip().lower()
    env_name = _BUCKET_ENV.get(key)
    if not env_name:
        return None, "unknown knowledge domain"
    bucket = (os.environ.get(env_name) or "").strip()
    if not bucket:
        return None, f"{env_name} not configured"
    if bucket.endswith("-mm") or "-mm-" in bucket:
        return None, "multimodal buckets are not allowed for ingest uploads"
    return bucket, None
