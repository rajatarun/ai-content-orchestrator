import json
import boto3
from logger import get_logger

log = get_logger("aws_secrets")
_secrets = boto3.client("secretsmanager")

def get_secret_json(secret_name: str) -> dict:
    if not secret_name:
        log.warning("get_secret_json called with empty secret_name")
        return {}

    log.info("Fetching secret value")
    resp = _secrets.get_secret_value(SecretId=secret_name)
    s = resp.get("SecretString") or ""
    if not s:
        log.warning("SecretString empty")
        return {}
    try:
        return json.loads(s)
    except Exception:
        log.warning("SecretString is not JSON; returning as {'value': ...}")
        return {"value": s}
