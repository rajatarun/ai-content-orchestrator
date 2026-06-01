import os
import json
import urllib.request
import urllib.error

from logger import get_logger, jdump
from aws_secrets import get_secret_json

log = get_logger("gemini_client")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, default)
    return v if v is not None else default

def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1]
        if "```" in t:
            t = t.rsplit("```", 1)[0]
    return json.loads(t.strip())

def gemini_generate_json(payload: dict, use_search: bool = True) -> dict:
    secret_name = _env("GEMINI_API_KEY_SECRET_NAME", "gemini/api_key")
    secret = get_secret_json(secret_name)
    api_key = secret.get("key") or secret.get("value")
    if not api_key:
        raise RuntimeError("Missing Gemini API key in Secrets Manager (expected {'key':'...'}).")

    model = _env("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    url = GEMINI_ENDPOINT.format(model=model)

    system = "Return valid JSON only (no markdown). Include credible source URLs for factual claims."
    body = {"contents":[{"parts":[{"text": system},{"text": json.dumps(payload, ensure_ascii=False)}]}]}
    if use_search:
        body["tools"] = [{"google_search": {}}]

    log.info("Calling Gemini", extra={"model": model, "use_search": use_search})

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type":"application/json","x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            log.info("Gemini response received", extra={"keys": list(data.keys())})
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="ignore")
        log.error("Gemini HTTPError", extra={"status": getattr(e, "code", None), "body": msg[:800]})
        raise RuntimeError("Gemini HTTPError {}: {}".format(getattr(e, "code", "NA"), msg)) from e
    except Exception:
        log.exception("Gemini call failed")
        raise

    candidates = data.get("candidates") or []
    if not candidates:
        log.error("Gemini returned no candidates", extra={"response": jdump(data)[:800]})
        raise RuntimeError("No candidates from Gemini")

    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    text = "".join([p.get("text","") for p in parts if isinstance(p, dict)])
    log.info("Gemini candidate text length", extra={"chars": len(text or "")})
    return _extract_json(text)
