# taskweave-content-orchestrator

AWS-based automation for a weekly content pipeline:
- Admin API to manage drafts, approvals, publishing, subscribers
- Gemini (with web search) to generate LinkedIn drafts
- Weekly newsletter generation and SES delivery
- DynamoDB-backed lifecycle + event tracking
- On approval, upload the article JSON to S3: `articles/{articleId}.json`

## Deploy
```bash
bash scripts/deploy.sh
```

## Prereqs
- AWS SAM CLI
- Python 3.9 runtime (Lambda)
- SES verified identity for sending email
- Gemini API key in Secrets Manager as JSON: { "key": "YOUR_GEMINI_API_KEY" }

Generated: 2026-02-28T00:55:34.825675Z
