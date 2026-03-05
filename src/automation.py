import json
import os
import time
import urllib.error
import urllib.request

import boto3

from db import add_event, get_article, update_article
from gemini_client import gemini_generate_json
from logger import get_logger
from statuses import AWAITING_APPROVAL

log = get_logger("automation")

s3 = boto3.client("s3")
stepfunctions = boto3.client("stepfunctions")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _build_bau_prompt(topic: str, objective: str) -> dict:
    return {
        "task": "linkedin_drafts",
        "instructions": [
            "Generate 3 distinct LinkedIn post drafts.",
            "Keep each draft concise, practical, and useful for engineering leaders.",
            "Return JSON only.",
        ],
        "context": {
            "topic": topic,
            "objective": objective,
            "channel": "linkedin",
            "audience": "engineering leaders",
        },
        "schema": {
            "drafts": [
                {
                    "variant": "string",
                    "linkedin_post": "string",
                    "hashtags": ["string"],
                    "cta_question": "string",
                }
            ]
        },
    }


def _normalize_drafts(payload: dict) -> list:
    drafts = payload.get("drafts") if isinstance(payload, dict) else None
    if not isinstance(drafts, list):
        return []
    clean = []
    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        post = (draft.get("linkedin_post") or draft.get("post") or "").strip()
        if not post:
            continue
        clean.append(
            {
                "variant": draft.get("variant", "Draft"),
                "post": draft.get("post", post),
                "linkedin_post": post,
                "hashtags": draft.get("hashtags") if isinstance(draft.get("hashtags"), list) else [],
                "char_count_estimate": draft.get("char_count_estimate") or len(post),
                "cta_question": draft.get("cta_question", ""),
            }
        )
    return clean


def createTeamTask(topic, objective):
    base_url = (os.environ.get("HTTP_API_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("HTTP_API_URL missing")

    payload = {
        "team": "tarun_visibility_team",
        "version": "v1",
        "request": {
            "topic": topic,
            "channel": "linkedin",
            "audience": "engineering leaders",
            "objective": objective,
        },
    }

    req = urllib.request.Request(
        f"{base_url}/team/task",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"team_task_http_{getattr(e, 'code', 'NA')}") from e
    except Exception as e:
        raise RuntimeError("team_task_failed") from e

    run_id = body.get("run_id") if isinstance(body, dict) else None
    execution_arn = body.get("execution_arn") if isinstance(body, dict) else None
    if not run_id or not execution_arn:
        try:
            response_preview = json.dumps(body, ensure_ascii=False)
        except Exception:
            response_preview = str(body)
        if len(response_preview) > 1000:
            response_preview = response_preview[:1000] + "..."
        log.error(
            "team_task_missing_ids",
            extra={
                "response_preview": response_preview,
                "has_run_id": bool(run_id),
                "has_execution_arn": bool(execution_arn),
            },
        )
        raise RuntimeError("team_task_missing_ids")

    return {"run_id": run_id, "execution_arn": execution_arn}


def pollExecution(executionArn):
    max_wait = _env_int("MAX_POLL_SECONDS", 90)
    interval = 1.0
    started = time.time()

    while True:
        resp = stepfunctions.describe_execution(executionArn=executionArn)
        status = resp.get("status")

        if status == "SUCCEEDED":
            return status
        if status in {"FAILED", "TIMED_OUT", "ABORTED"}:
            return status

        elapsed = time.time() - started
        if elapsed >= max_wait:
            return "RUNNING"

        time.sleep(interval)
        interval = min(interval + 1.0, 5.0)


def fetchWriterArtifact(runId):
    bucket = os.environ.get("ARTIFACT_BUCKET")
    if not bucket:
        raise RuntimeError("ARTIFACT_BUCKET missing")

    prefix = f"runs/{runId}/"
    token = None
    key = None

    while True:
        args = {"Bucket": bucket, "Prefix": prefix}
        if token:
            args["ContinuationToken"] = token

        resp = s3.list_objects_v2(**args)
        for item in resp.get("Contents", []):
            item_key = item.get("Key", "")
            if item_key.endswith("writer_linkedin.json"):
                key = item_key
                break

        if key:
            break

        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    if not key:
        raise RuntimeError("writer_artifact_not_found")

    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read().decode("utf-8")
    parsed = json.loads(raw)
    drafts = _normalize_drafts(parsed)
    if not drafts:
        raise RuntimeError("writer_artifact_invalid")

    return drafts


def fallbackToBAUGeneration(topic, objective):
    prompt = _build_bau_prompt(topic, objective)
    result = gemini_generate_json(prompt, use_search=False)
    drafts = _normalize_drafts(result)
    if not drafts:
        raise RuntimeError("bau_empty_drafts")
    return drafts


def _coerce_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return "\n".join(parts)
    return ""


def _resolve_generation_inputs(event: dict):
    article_id = (event or {}).get("articleId")
    topic = (event or {}).get("topic")
    objective = (event or {}).get("objective")
    article = None

    if article_id:
        article = get_article(article_id)
        if not article:
            raise RuntimeError("article_not_found")
        topic = topic or article.get("title") or ""
        objective = objective or article.get("sourceInputs") or ""

    topic = _coerce_text(topic)
    objective = _coerce_text(objective)
    if not topic or not objective:
        raise RuntimeError("topic_and_objective_required")

    return article_id, article, topic, objective


def _persist_generation_result(article_id: str, current_article: dict, drafts: list, source: str, status: str):
    if not article_id:
        return

    first = drafts[0] if drafts else {}
    generated = {
        "source": source,
        "status": status,
        "drafts": drafts,
        "linkedin_post": first.get("linkedin_post", ""),
        "hashtags": first.get("hashtags", []),
        "cta_question": first.get("cta_question", ""),
    }

    patch = {"generated": generated, "status": AWAITING_APPROVAL}

    update_article(article_id, patch)
    add_event(article_id, "GENERATED", f"Generation complete using {source}; moved to {AWAITING_APPROVAL}")


def generate_drafts_handler(event, context):
    event = event or {}
    article_id = None
    article = None

    try:
        article_id, article, topic, objective = _resolve_generation_inputs(event)
    except Exception:
        log.exception("invalid_generate_request")
        return {"ok": False, "error": "Invalid request"}

    try:
        task = createTeamTask(topic, objective)
        run_id = task["run_id"]
        execution_arn = task["execution_arn"]
        log.info("teamweave_started", extra={"run_id": run_id, "execution_arn": execution_arn[:48]})

        status = pollExecution(execution_arn)
        if status != "SUCCEEDED":
            raise RuntimeError(f"execution_{status.lower()}")

        drafts = fetchWriterArtifact(run_id)
        response = {
            "source": "teamweave",
            "status": "SUCCEEDED",
            "run_id": run_id,
            "execution_arn": execution_arn,
            "drafts": drafts,
        }
    except Exception:
        log.exception("teamweave_failed_using_bau")
        try:
            drafts = fallbackToBAUGeneration(topic, objective)
        except Exception:
            log.exception("bau_generation_failed")
            if article_id:
                add_event(article_id, "GENERATE_FAILED", "Both TeamWeave and BAU generation failed")
            return {"ok": False, "error": "Generation failed"}

        response = {
            "source": "bau",
            "status": "FALLBACK",
            "drafts": drafts,
        }

    try:
        _persist_generation_result(article_id, article, response.get("drafts", []), response["source"], response["status"])
    except Exception:
        log.exception("persist_generation_failed")

    return response
