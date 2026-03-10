# src/site_data.py
import os
import json
import boto3
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

from logger import get_logger

log = get_logger("site_data")
s3 = boto3.client("s3")


def _cors_headers(event):
    hdrs = event.get("headers") or {}
    origin = hdrs.get("origin") or hdrs.get("Origin") or "*"
    allowed = os.environ.get("ALLOWED_ORIGIN", "*")
    allow_origin = origin if allowed == "*" or origin == allowed else allowed
    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Headers": "content-type,authorization,x-api-key,accept",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Vary": "Origin",
    }


def _resp(event, code, body):
    h = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    h.update(_cors_headers(event))
    return {"statusCode": code, "headers": h, "body": json.dumps(body, ensure_ascii=False, default=str)}


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
    # HTTP API (v2) fallback
    return parse_qs(event.get("rawQueryString") or "")


def _make_excerpt(text: str, max_len: int = 200) -> str:
    if not text:
        return ""
    # Collapse whitespace/newlines/bullets into a clean snippet
    t = (text or "").replace("\u2022", " ").replace("•", " ")
    t = " ".join(t.split())
    # Fix common mojibake artifacts if they slipped in
    t = t.replace("â€™", "’").replace("â€“", "–").replace("â€”", "—").replace("â€œ", "“").replace("â€�", "”")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _list_objects(bucket: str, prefix: str):
    paginator = s3.get_paginator("list_objects_v2")
    objs = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for it in page.get("Contents", []):
            if it.get("Key", "").endswith(".json"):
                objs.append(it)
    # Newest first
    objs.sort(key=lambda x: x["LastModified"], reverse=True)
    return objs


def _get_json(bucket: str, key: str) -> dict:
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read().decode("utf-8")
    return json.loads(raw)


def _blog_card_from_article(article: dict, fallback_id: str, fallback_last_modified: str) -> dict:
    # Your stored JSON structure:
    # {
    #   "entityType": "ARTICLE",
    #   "id": "...",
    #   "title": "...",
    #   "tags": [...],
    #   "status": "APPROVED" | "PUBLISHED" | ...,
    #   "createdAt": "...",
    #   "updatedAt": "...",
    #   "publishedAt": "...?" ,
    #   "publishedUrl": "...?" ,
    #   "generated": { "linkedin_post": "...", ... }
    # }

    aid = article.get("id") or fallback_id
    title = (article.get("title") or "").strip() or "Untitled"
    tags = article.get("tags") or []
    status = (article.get("status") or "").strip()

    published_at = (
        (article.get("publishedAt") or "").strip()
        or (article.get("updatedAt") or "").strip()
        or (article.get("createdAt") or "").strip()
        or fallback_last_modified
    )

    url = (article.get("publishedUrl") or "").strip()
    linkedin_post = ((article.get("generated") or {}).get("linkedin_post") or "").strip()
    excerpt = _make_excerpt(linkedin_post, 200)

    return {
        "id": aid,
        "title": title,
        "excerpt": excerpt,
        "publishedAt": published_at,
        "url": url,
        "tags": tags,
        "status": status,
    }


def _route_path(event) -> str:
    # Works for REST API and HTTP API
    return event.get("path") or event.get("rawPath") or ""


def lambda_handler(event, context):
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
    path = _route_path(event)

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(event), "body": ""}

    if method != "GET":
        return _resp(event, 405, {"error": "Method not allowed"})

    bucket = os.environ["ARTICLES_BUCKET"]
    prefix = os.environ.get("ARTICLES_PREFIX", "docs/articles").rstrip("/") + "/"
    qs = _qs(event)

    # GET /site/posts -> blog cards
    if path == "/site/posts":
        limit = int((qs.get("limit") or ["20"])[0])
        limit = max(1, min(limit, 50))

        concurrency = int((qs.get("concurrency") or ["5"])[0])
        concurrency = max(1, min(concurrency, 10))

        allowed_status = set((qs.get("status") or ["APPROVED,PUBLISHED"])[0].split(","))
        allowed_status = {s.strip().upper() for s in allowed_status if s.strip()}

        objs = _list_objects(bucket, prefix)[:limit]

        errors = 0
        cards = []

        def fetch_and_card(o):
            key = o["Key"]
            aid = key.rsplit("/", 1)[-1].replace(".json", "")
            last_mod = o["LastModified"].isoformat()
            article = _get_json(bucket, key)
            return _blog_card_from_article(article, fallback_id=aid, fallback_last_modified=last_mod)

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = [ex.submit(fetch_and_card, o) for o in objs]
            for fut in as_completed(futs):
                try:
                    cards.append(fut.result())
                except Exception:
                    errors += 1
                    log.exception("card_build_failed")

        # Filter to only approved/published by default (public-safe)
        cards = [c for c in cards if (c.get("status") or "").upper() in allowed_status]

        # Sort by publishedAt descending (ISO string sort is OK here)
        cards.sort(key=lambda x: x.get("publishedAt", ""), reverse=True)

        return _resp(event, 200, {
            "items": cards,
            "count": len(cards),
            "errors": errors,
            "bucket": bucket,
            "prefix": prefix,
        })

    # GET /site/posts/{id} -> full JSON
    if path.startswith("/site/posts/"):
        aid = path.split("/site/posts/", 1)[1].strip()
        if not aid:
            return _resp(event, 400, {"error": "id required"})
        key = f"{prefix}{aid}.json"
        try:
            data = _get_json(bucket, key)
            return _resp(event, 200, data)
        except s3.exceptions.NoSuchKey:
            return _resp(event, 404, {"error": "Not found"})
        except Exception as e:
            log.exception("get_post_failed", extra={"id": aid, "key": key})
            return _resp(event, 500, {"error": "Failed to read post", "details": str(e)})

    return _resp(event, 404, {"error": "Route not found"})