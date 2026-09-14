#!/usr/bin/env python3
"""Resolve every API and storage coordinate a harness needs from the stack.

Before this, a test or script that wanted to exercise this API had to know four
things the repository did not tell it: the API base URL, the DynamoDB table
name, **the names of the two GSIs** (a table name alone does not let you query a
table whose access pattern is an index -- you either name the index or you
scan), and the S3 bucket holding published articles, which was a literal with
the account id baked into it, repeated in five places in template.yaml.

All of them are stack Outputs now. ``tests/test_openapi_contract.py`` asserts
that every output named here still exists in ``template.yaml``, so removing one
fails at the commit that removes it rather than at the next automation run.

Usage
-----
    python scripts/stack_env.py --stack tarun-admin-content
    eval "$(python scripts/stack_env.py --stack tarun-admin-content --format sh)"

    curl -sS -H "x-api-key: $KEY" -H "Authorization: Bearer $JWT" \
         "$ACO_API_BASE/admin/articles?status=DRAFT"

Credentials are deliberately not resolved here. Every /admin route needs both
an API key and a SIWE bearer token; neither is a stack output and neither
should pass through a shell environment on the way to a log.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

REQUIRED_OUTPUTS = {
    "ACO_API_BASE": "ApiBaseUrl",
    "ACO_CONTENT_TABLE": "ContentTableName",
    "ACO_STATUS_UPDATED_INDEX": "ContentTableStatusUpdatedIndex",
    "ACO_STATUS_PUBLISHED_INDEX": "ContentTableStatusPublishedIndex",
    "ACO_ARTICLES_BUCKET": "ArticlesBucketName",
    "ACO_ARTICLES_PREFIX": "ArticlesPrefix",
}

OPTIONAL_OUTPUTS = {
    "ACO_REST_API_ID": "RestApiId",
    "ACO_REST_API_LOG_GROUP": "RestApiLogGroupName",
    "ACO_ARTIFACT_BUCKET": "ArtifactBucketName",
    # Not needed to call the API -- published so a harness can assert what the
    # stack is actually deployed with, which is not necessarily the template
    # default. See the GeminiModelId output's own description.
    "ACO_GEMINI_MODEL": "GeminiModelId",
    "ACO_GENERATE_FN": "GenerateDraftsFunctionName",
    "ACO_NEWSLETTER_FN": "NewsletterFunctionName",
    "ACO_APPROVAL_TOPIC_ARN": "ApprovalNotificationTopicArn",
    "ACO_OPENAPI_SPEC": "OpenApiSpecPath",
}


class MissingOutputs(RuntimeError):
    """The stack exists but does not publish something a harness needs."""


def build_env(outputs: dict) -> dict:
    """Map stack outputs onto environment variable names (pure, so it is testable)."""
    env = {}
    missing = []
    for var, key in REQUIRED_OUTPUTS.items():
        if outputs.get(key):
            env[var] = outputs[key]
        else:
            missing.append(key)
    if missing:
        raise MissingOutputs(
            "stack publishes no value for: " + ", ".join(sorted(missing))
            + ". Deploy a template that exports them (see the Outputs section of "
            "template.yaml) -- do not hardcode them in the harness."
        )
    for var, key in OPTIONAL_OUTPUTS.items():
        if outputs.get(key):
            env[var] = outputs[key]
    return env


def fetch_outputs(stack: str, region: str | None) -> dict:
    try:
        import boto3  # noqa: PLC0415 -- optional; the CLI path covers images without it
    except ImportError:
        boto3 = None

    if boto3 is not None:
        cfn = boto3.client("cloudformation", region_name=region) if region else boto3.client("cloudformation")
        stacks = cfn.describe_stacks(StackName=stack)["Stacks"]
    else:
        cmd = ["aws", "cloudformation", "describe-stacks", "--stack-name", stack, "--output", "json"]
        if region:
            cmd += ["--region", region]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}")
        stacks = json.loads(proc.stdout)["Stacks"]

    return {o["OutputKey"]: o.get("OutputValue", "") for o in stacks[0].get("Outputs", [])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Resolve API and storage coordinates from the stack")
    ap.add_argument("--stack", default="tarun-admin-content", help="CloudFormation stack name")
    ap.add_argument("--region", default=None)
    ap.add_argument("--format", choices=("json", "sh"), default="json")
    args = ap.parse_args(argv)

    try:
        env = build_env(fetch_outputs(args.stack, args.region))
    except (MissingOutputs, RuntimeError) as exc:
        print(f"stack_env: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(env, indent=2, sort_keys=True))
    else:
        for var in sorted(env):
            print(f"export {var}={shlex.quote(env[var])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
