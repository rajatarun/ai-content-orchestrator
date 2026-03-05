#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-taskweave-content-orchestrator}"
TEAM_STACK_NAME="${TEAM_STACK_NAME:-tarun-content-team}"

artifact_bucket="$(aws cloudformation describe-stacks \
  --region "$REGION" \
  --stack-name "$TEAM_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucket'].OutputValue" \
  --output text)"

status_function_arn="$(aws cloudformation describe-stacks \
  --region "$REGION" \
  --stack-name "$TEAM_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='StatusFunctionArn'].OutputValue" \
  --output text)"

http_api_url="$(aws cloudformation describe-stacks \
  --region "$REGION" \
  --stack-name "$TEAM_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='HttpApiUrl'].OutputValue" \
  --output text)"

if [[ -z "$artifact_bucket" || "$artifact_bucket" == "None" ]]; then
  echo "Error: ArtifactBucket output not found in stack $TEAM_STACK_NAME" >&2
  exit 1
fi

if [[ -z "$status_function_arn" || "$status_function_arn" == "None" ]]; then
  echo "Error: StatusFunctionArn output not found in stack $TEAM_STACK_NAME" >&2
  exit 1
fi

if [[ -z "$http_api_url" || "$http_api_url" == "None" ]]; then
  echo "Error: HttpApiUrl output not found in stack $TEAM_STACK_NAME" >&2
  exit 1
fi

rm -rf .aws-sam || true
sam build
sam deploy --guided \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --parameter-overrides \
    TeamArtifactBucket="$artifact_bucket" \
    TeamStatusFunctionArn="$status_function_arn" \
    TeamHttpApiUrl="$http_api_url"
