from typing import Optional, Dict, Any, List
import os
import time
import uuid
import boto3
from boto3.dynamodb.conditions import Key, Attr
from logger import get_logger

log = get_logger("db")
ddb = boto3.resource("dynamodb")

def _t():
    return ddb.Table(os.environ["TABLE_NAME"])

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def new_id():
    return str(uuid.uuid4())

def _strip_none_and_empty(item: dict) -> dict:
    # DynamoDB cannot store empty strings for key attributes (including GSI keys).
    return {k: v for k, v in item.items() if v is not None and v != ""}

def put_article(payload: dict) -> dict:
    aid = payload.get("id") or new_id()
    created = now_iso()
    item = {
        "pk": "ARTICLE",
        "sk": aid,
        "id": aid,
        "entityType": "ARTICLE",
        "title": payload.get("title",""),
        "sourceInputs": payload.get("sourceInputs",""),
        "tags": payload.get("tags", []),
        "status": payload.get("status","DRAFT"),
        "createdAt": created,
        "updatedAt": created,
        "scheduledAt": payload.get("scheduledAt"),
        "publishedAt": payload.get("publishedAt"),
        "publishedUrl": payload.get("publishedUrl"),
        "revisionNote": payload.get("revisionNote",""),
        "generated": payload.get("generated", {}),
        "meta": payload.get("meta", {"version": 1, "retryCount": 0, "lastError": ""}),
    }
    item = _strip_none_and_empty(item)
    log.info("put_article", extra={"id": aid, "status": item.get("status")})
    _t().put_item(Item=item)
    add_event(aid, "CREATED", "Article created")
    return item

def get_article(aid: str) -> Optional[Dict[str, Any]]:
    resp = _t().get_item(Key={"pk":"ARTICLE","sk":aid})
    return resp.get("Item")

def update_article(aid: str, patch: dict) -> dict:
    item = get_article(aid)
    if not item:
        raise KeyError("Not found")
    for k, v in patch.items():
        if k == "meta" and isinstance(v, dict):
            m = item.get("meta") or {}
            m.update(v)
            item["meta"] = m
        else:
            item[k] = v
    item["updatedAt"] = now_iso()
    item.setdefault("meta", {})
    try:
        item["meta"]["version"] = int(item["meta"].get("version", 1)) + 1
    except Exception:
        item["meta"]["version"] = 1
    item = _strip_none_and_empty(item)
    log.info("update_article", extra={"id": aid, "status": item.get("status"), "version": item.get("meta", {}).get("version")})
    _t().put_item(Item=item)
    return item

def list_by_status(status: str, limit: int = 20) -> List[Dict[str, Any]]:
    log.info("list_by_status", extra={"status": status, "limit": limit})
    resp = _t().query(
        IndexName="StatusUpdatedIndex",
        KeyConditionExpression=Key("status").eq(status),
        ScanIndexForward=False,
        Limit=limit
    )
    return resp.get("Items", [])

def list_published_between(date_from: str, date_to: str, limit: int = 50) -> List[Dict[str, Any]]:
    log.info("list_published_between", extra={"from": date_from, "to": date_to, "limit": limit})
    resp = _t().query(
        IndexName="StatusPublishedIndex",
        KeyConditionExpression=Key("status").eq("PUBLISHED") & Key("publishedAt").between(date_from, date_to),
        ScanIndexForward=False,
        Limit=limit
    )
    return resp.get("Items", [])

def add_event(article_id: str, etype: str, message: str):
    log.info("add_event", extra={"articleId": article_id, "type": etype})
    _t().put_item(Item={
        "pk": f"EVENT#{article_id}",
        "sk": now_iso(),
        "entityType": "EVENT",
        "articleId": article_id,
        "type": etype,
        "message": message
    })

def list_events(article_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    resp = _t().query(
        KeyConditionExpression=Key("pk").eq(f"EVENT#{article_id}"),
        ScanIndexForward=False,
        Limit=limit
    )
    return resp.get("Items", [])

def put_subscriber(email: str) -> dict:
    e = (email or "").strip().lower()
    item = {"pk":"SUBSCRIBER","sk":e,"entityType":"SUBSCRIBER","email":e,"status":"ACTIVE","createdAt":now_iso()}
    log.info("put_subscriber", extra={"email": e})
    _t().put_item(Item=item)
    return item

def list_subscribers(limit: int = 500) -> List[Dict[str, Any]]:
    log.info("list_subscribers", extra={"limit": limit})
    resp = _t().scan(
        FilterExpression=Attr("pk").eq("SUBSCRIBER"),
        Limit=limit
    )
    return resp.get("Items", [])

def delete_subscriber(email: str):
    e = (email or "").strip().lower()
    log.info("delete_subscriber", extra={"email": e})
    _t().delete_item(Key={"pk":"SUBSCRIBER","sk":e})
