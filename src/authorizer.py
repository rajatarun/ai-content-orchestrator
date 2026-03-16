import json
import os
import boto3

lambda_client = boto3.client("lambda")


def lambda_handler(event, context):
    upstream_arn = os.environ["UPSTREAM_AUTHORIZER_ARN"]

    response = lambda_client.invoke(
        FunctionName=upstream_arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )

    result = json.loads(response["Payload"].read().decode("utf-8"))

    # Widen every statement's Resource to a wildcard so the cached policy
    # is valid for all API endpoints, not just the one that was first called.
    policy = result.get("policyDocument")
    if policy:
        for statement in policy.get("Statement", []):
            statement["Resource"] = "arn:aws:execute-api:*:*:*/*"

    return result
