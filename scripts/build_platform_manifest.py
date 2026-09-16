#!/usr/bin/env python3
"""Build the platform-wide product manifest the public /platform page reads.

This repo's deploy job already assumes the account-wide deploy role
(``teamweave-github-actions-sam-deployer``) and already calls
``cloudformation:DescribeStacks`` on stacks it does not own -- TeamWeave's
and SIWE's, to resolve parameters this stack's own deploy needs (see "Load
shared stack outputs" in .github/workflows/deploy.yml). This script extends
that same already-proven pattern to a couple more stacks, purely to publish
their public API base URLs -- it does not gate or feed this repo's own
deploy in any way, so a failed lookup here must never fail that deploy.

Every live entry is resolved from a real CloudFormation Outputs block, the
same way scripts/stack_env.py resolves this repo's own -- never
hand-typed. A stack this script has no reachable access to (wrong role
scope, stack renamed, region mismatch) degrades that one product to
``apiBaseUrl: null`` rather than failing the whole manifest; a directory
that goes stale in one entry is still useful, a manifest that refuses to
build because one of sixteen lookups failed is not.

Products with no CloudFormation stack tracked here at all (most of the
portfolio) are listed from the static entries below -- name, repo,
description, category -- with apiBaseUrl left null. That is not a
placeholder to fill in later; several of these (mcp-observatory, ToolWeave,
CipherWeave, ...) are libraries/MCP servers with no public HTTP surface to
publish here in the first place. IntentWeave and PromptWeave are marked
archived-style with a `status` field rather than folded into the working
list -- both are reserved repo names with no code (see their own READMEs).

Usage
-----
    python scripts/build_platform_manifest.py \
        --own-api-base-url "$OWN_API_BASE_URL" \
        --team-api-base-url "$TEAM_HTTP_API_URL" \
        --siwe-api-base-url "$SIWE_API_BASE_URL" \
        --contextweave-stack contextweave-rag-prod \
        --deviceweave-stack deviceweave-prod \
        --region us-east-1 \
        --out docs/platform-manifest.json

Every one of the three ``--*-api-base-url`` flags is a plain scalar the
caller already resolved (this repo's deploy job resolves TEAM_HTTP_API_URL
and, with the SIWE extraction below, SIWE_API_BASE_URL, while deploying its
own stack) -- passed straight through rather than threaded as a JSON blob
through $GITHUB_ENV, which is unnecessary complexity for one string each.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

GITHUB_ORG = "rajatarun"

# name -> (repo, category, description, openapi spec path relative to repo root or None)
STATIC_PRODUCTS = {
    "ai-content-orchestrator": (
        "ai-content-orchestrator", "Orchestration",
        "AWS serverless pipeline: article lifecycle, AI LinkedIn drafts via Gemini, weekly newsletter via SES.",
        "openapi/content-orchestrator.yaml",
    ),
    "TeamWeave": (
        "TeamWeave", "Orchestration",
        "Config-driven multi-agent orchestration platform on AWS using Step Functions, Bedrock, and DynamoDB.",
        "openapi/teamweave.yaml",
    ),
    "RoutineWeave": (
        "RoutineWeave", "Orchestration",
        "AI-powered scheduled execution engine -- JSON task prompts run on a cron schedule via Gemini, delivered through SNS.",
        None,
    ),
    "ContextWeave": (
        "ContextWeave", "Knowledge / RAG",
        "AWS-native GraphRAG + CAG knowledge layer: Memgraph expertise graph, pgvector chunks, Neptune-backed adaptive routing.",
        "openapi/contextweave.yaml",
    ),
    "AuthChain": (
        "AuthChain", "Security",
        "Serverless Sign-In With Ethereum (SIWE) gateway issuing JWT sessions, with Bedrock-backed RAG document Q&A.",
        None,
    ),
    "CipherWeave": (
        "CipherWeave", "Security",
        "Agentic cryptography intelligence layer -- policy-enforced, explainable encryption strategy for AI agents.",
        None,
    ),
    "mcp-observatory": (
        "mcp-observatory", "Security",
        "The shared propose/commit safety gate: risk-scores a tool call before it runs, used across this platform.",
        None,
    ),
    "ToolWeave": (
        "ToolWeave", "Execution / Tools",
        "FastMCP server that turns natural-language requests into safe REST API executions from OpenAPI/Swagger specs.",
        None,
    ),
    "DeviceWeave": (
        "DeviceWeave", "Execution / Tools",
        "AI-native execution layer converting human intent into safe, real-time control of physical IoT environments.",
        "openapi/deviceweave.yaml",
    ),
    "ScreenWeave": (
        "ScreenWeave", "Execution / Tools",
        "AWS-native website crawling and visual QA platform -- Playwright capture plus a Bedrock-driven anomaly report.",
        None,
    ),
    "DataDictionary": (
        "DataDictionary", "Data",
        "MCP server storing/retrieving API field definitions, with AI-assisted drafts and mcp-observatory-gated writes.",
        None,
    ),
    "TrainWeave": (
        "TrainWeave", "Deploy",
        "Ephemeral EC2 Spot LoRA fine-tuning orchestration -- one Lambda, no SageMaker, no idle compute.",
        None,
    ),
    "DeployWeave": (
        "DeployWeave", "Deploy",
        "Dynamic model selection, Bedrock agent provisioning, LoRA adapter management, real-time token enforcement.",
        None,
    ),
    "TaskWeave": (
        "TaskWeave", "Orchestration",
        "Archived. The first JSON-driven agent framework in this portfolio; superseded by TeamWeave.",
        None,
    ),
}

# name -> (repo, capability lives in ...)
RESERVED_PRODUCTS = {
    "IntentWeave": ("IntentWeave", "DeviceWeave + ToolWeave"),
    "PromptWeave": ("PromptWeave", "TeamWeave, RoutineWeave, ai-content-orchestrator"),
}

ARCHIVED = {"TaskWeave"}


def describe_stack_outputs(stack: str, region: str) -> dict:
    """Best-effort describe-stacks. Returns {} on any failure -- never raises."""
    cmd = [
        "aws", "cloudformation", "describe-stacks",
        "--stack-name", stack, "--region", region, "--output", "json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001 -- best-effort, log and move on
        print(f"build_platform_manifest: describe-stacks {stack} failed to run: {exc}", file=sys.stderr)
        return {}
    if proc.returncode != 0:
        print(f"build_platform_manifest: describe-stacks {stack} exited {proc.returncode}: {proc.stderr.strip()}", file=sys.stderr)
        return {}
    try:
        stacks = json.loads(proc.stdout)["Stacks"]
        return {o["OutputKey"]: o.get("OutputValue", "") for o in stacks[0].get("Outputs", [])}
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"build_platform_manifest: describe-stacks {stack} returned unexpected shape: {exc}", file=sys.stderr)
        return {}


def repo_url(repo: str) -> str:
    return f"https://github.com/{GITHUB_ORG}/{repo}"


def openapi_raw_url(repo: str, path: str | None) -> str | None:
    if not path:
        return None
    return f"https://raw.githubusercontent.com/{GITHUB_ORG}/{repo}/main/{path}"


def clean(value: str | None) -> str | None:
    """"" and the literal string "None" both mean absent here: an empty CLI
    flag comes through as "", and `aws ... --output text` on a null JMESPath
    result prints the literal word None rather than an empty string."""
    if not value or value == "None":
        return None
    return value


def build_manifest(
    own_api_base_url: str | None,
    team_api_base_url: str | None,
    siwe_api_base_url: str | None,
    contextweave_outputs: dict,
    deviceweave_outputs: dict,
) -> dict:
    live_api_base = {
        "ai-content-orchestrator": clean(own_api_base_url),
        "TeamWeave": clean(team_api_base_url),
        "AuthChain": clean(siwe_api_base_url),
        "ContextWeave": contextweave_outputs.get("APIEndpoint"),
        "DeviceWeave": deviceweave_outputs.get("ApiBaseUrl"),
    }

    products = []
    for name, (repo, category, description, spec_path) in STATIC_PRODUCTS.items():
        products.append({
            "name": name,
            "repo": repo_url(repo),
            "category": category,
            "description": description,
            "apiBaseUrl": live_api_base.get(name) or None,
            "openApiSpecUrl": openapi_raw_url(repo, spec_path),
            "status": "archived" if name in ARCHIVED else "active",
        })

    for name, (repo, lives_in) in RESERVED_PRODUCTS.items():
        products.append({
            "name": name,
            "repo": repo_url(repo),
            "category": "Reserved",
            "description": f"Reserved, not started. Capability lives in {lives_in}.",
            "apiBaseUrl": None,
            "openApiSpecUrl": None,
            "status": "reserved",
        })

    products.sort(key=lambda p: (p["category"], p["name"]))

    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "ai-content-orchestrator/.github/workflows/deploy.yml",
        "schemaVersion": 1,
        "products": products,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--own-api-base-url", default=None, help="This stack's own ApiBaseUrl output")
    ap.add_argument("--team-api-base-url", default=None, help="tarun-content-team's HttpApiUrl output")
    ap.add_argument("--siwe-api-base-url", default=None, help="siwe-infra's ApiBaseUrl output")
    ap.add_argument("--contextweave-stack", default="contextweave-rag-prod")
    ap.add_argument("--deviceweave-stack", default="deviceweave-prod")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--skip-cross-account-lookups", action="store_true",
                     help="Skip the ContextWeave/DeviceWeave describe-stacks calls (for local dry-runs / tests)")
    ap.add_argument("--out", default="docs/platform-manifest.json")
    args = ap.parse_args(argv)

    if args.skip_cross_account_lookups:
        contextweave_outputs, deviceweave_outputs = {}, {}
    else:
        contextweave_outputs = describe_stack_outputs(args.contextweave_stack, args.region)
        deviceweave_outputs = describe_stack_outputs(args.deviceweave_stack, args.region)

    manifest = build_manifest(
        args.own_api_base_url, args.team_api_base_url, args.siwe_api_base_url,
        contextweave_outputs, deviceweave_outputs,
    )

    with open(args.out, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=False)
        f.write("\n")

    live_count = sum(1 for p in manifest["products"] if p["apiBaseUrl"])
    print(f"build_platform_manifest: wrote {args.out} -- {len(manifest['products'])} products, {live_count} with a live apiBaseUrl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
