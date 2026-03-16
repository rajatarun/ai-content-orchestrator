import os
import hmac

from logger import get_logger

log = get_logger("authorizer")

JWT_SECRET = os.environ.get("JWT_SECRET", "")


def _extract_wildcard_arn(method_arn: str) -> str:
    """
    Extract a stage-level wildcard ARN from a full method ARN.

    Input:  arn:aws:execute-api:{region}:{account}:{apiId}/{stage}/{method}/{resource}
    Output: arn:aws:execute-api:{region}:{account}:{apiId}/{stage}/*
    """
    # method_arn format:
    #   arn:aws:execute-api:{region}:{account}:{apiId}/{stage}/{httpMethod}/{resource}
    # Split on ':' gives:
    #   ['arn', 'aws', 'execute-api', '{region}', '{account}', '{apiId}/{stage}/...']
    parts = method_arn.split(":")
    region = parts[3]
    account = parts[4]
    # parts[5] = {apiId}/{stage}/{httpMethod}/{resource...}
    gateway_parts = parts[5].split("/")
    api_id = gateway_parts[0]
    stage = gateway_parts[1]
    return f"arn:aws:execute-api:{region}:{account}:{api_id}/{stage}/*"


def _build_policy(principal_id: str, effect: str, resource: str) -> dict:
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }


def _validate_token(token: str) -> str | None:
    """
    Validate a bearer token and return a principal ID on success, or None on failure.
    Uses constant-time comparison to prevent timing attacks.
    """
    if not JWT_SECRET or not token:
        log.warning("JWT_SECRET not configured or token missing — denying request")
        return None

    if hmac.compare_digest(token, JWT_SECRET):
        return "admin"
    return None


def lambda_handler(event, context):
    log.info("authorizer invoked", extra={"requestId": context.aws_request_id})

    method_arn = event.get("methodArn", "")
    wildcard_arn = _extract_wildcard_arn(method_arn)

    log.info(
        "ARN extracted",
        extra={"methodArn": method_arn, "wildcardArn": wildcard_arn},
    )

    auth_header = (
        event.get("headers", {}) or {}
    ).get("Authorization") or (
        event.get("headers", {}) or {}
    ).get("authorization", "")

    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]

    principal = _validate_token(token)

    if principal:
        log.info("request authorized", extra={"principal": principal})
        return _build_policy(principal, "Allow", wildcard_arn)

    log.warning("request denied — invalid token")
    return _build_policy("unauthorized", "Deny", wildcard_arn)
