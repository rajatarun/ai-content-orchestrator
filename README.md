# taskweave-content-orchestrator

AWS-based automation for a weekly content pipeline:
- Admin API to manage drafts, approvals, publishing, subscribers
- Gemini (with web search) to generate LinkedIn drafts
- Weekly newsletter generation and SES delivery
- DynamoDB-backed lifecycle + event tracking
- On approval, upload the article JSON to S3: `articles/{articleId}.json`

## Deploy
```bash
# Optional override for the source team stack
TEAM_STACK_NAME=tarun-content-team bash scripts/deploy.sh
```

The deploy script fetches `ArtifactBucket`, `StatusFunctionArn`, and `HttpApiUrl` from the team stack outputs and passes them as SAM parameter overrides.

## Prereqs
- AWS SAM CLI
- AWS CLI
- Python 3.9 runtime (Lambda)
- SES verified identity for sending email
- Gemini API key in Secrets Manager as JSON: { "key": "YOUR_GEMINI_API_KEY" }

Generated: 2026-02-28T00:55:34.825675Z

## Article lifecycle statuses
- `DRAFT`
- `REVISION_REQUESTED`
- `AWAITING_APPROVAL`
- `APPROVED`
- `PUBLISHED`
- `FAILED`
- `ARCHIVED`

The admin API now supports explicit actions for `submit-for-approval`, `mark-failed`, and `archive`.
