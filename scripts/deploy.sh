#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-taskweave-content-orchestrator}"

rm -rf .aws-sam || true
sam build
sam deploy --guided --region "$REGION" --stack-name "$STACK_NAME"
