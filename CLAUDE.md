# CLAUDE.md — TaskWeave Content Orchestrator

## Project Overview

AWS serverless application that automates a weekly content publishing pipeline:
- Admin API for article lifecycle management (draft → approval → publish)
- AI-powered LinkedIn draft generation via Google Gemini
- Weekly newsletter generation and delivery via AWS SES
- DynamoDB-backed article/event/subscriber storage

## Tech Stack

- **Runtime:** Python 3.11 on AWS Lambda
- **Infrastructure:** AWS SAM (CloudFormation)
- **AWS Services:** Lambda, DynamoDB, S3, SES, EventBridge, SNS, Secrets Manager
- **External APIs:** Google Gemini (content generation), Google Search

## Repository Structure

```
src/                  # Lambda function source code
  admin_api.py        # Article management endpoints
  public_api.py       # Newsletter subscribe endpoint
  site_data.py        # Public articles API
  appointment_api.py  # Appointment booking
  automation.py       # Weekly draft generation logic
  newsletter.py       # Newsletter generation + SES delivery
  publisher.py        # S3 upload utility
  db.py               # All DynamoDB operations
  gemini_client.py    # Gemini API wrapper
  aws_secrets.py      # Secrets Manager helper
  mailer.py           # SES email helper
  logger.py           # JSON-structured logging
  statuses.py         # Article status constants
  requirements.txt    # Python deps for Lambda
template.yaml         # SAM infrastructure definition
scripts/deploy.sh     # Local deployment script
.github/workflows/    # CI/CD (GitHub Actions + OIDC)
```

## Build & Deploy

```bash
# Build and deploy (guided first run)
sam build
sam deploy --guided --region us-east-1 --stack-name taskweave-content-orchestrator

# Or use the convenience script
bash scripts/deploy.sh
```

The CI/CD pipeline (`.github/workflows/deploy.yml`) deploys automatically on push to `main`.

## Local Development

```bash
# Install dependencies
pip install -r src/requirements.txt

# Invoke a Lambda handler locally with a sample event
sam local invoke AdminApiFunction --event <event.json>
```

No automated test suite exists yet. Lambda handlers can be tested by passing JSON event payloads directly.

## Key Configuration

SAM template parameters (set at deploy time or in CI secrets):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `GeminiApiKeySecretName` | `gemini/api_key` | Secrets Manager key for Gemini API |
| `GeminiModel` | `gemini-3-flash-preview` | Gemini model to use |
| `SesFromEmail` | `rajatarun12@gmail.com` | Verified SES sender address |
| `ArtifactBucket` | (shared stack) | S3 bucket for published articles |

Gemini API key must be stored in Secrets Manager as: `{ "key": "YOUR_GEMINI_API_KEY" }`

## Article Lifecycle

```
DRAFT → AWAITING_APPROVAL → APPROVED → PUBLISHED
         ↓                    ↓
  REVISION_REQUESTED       ARCHIVED / FAILED
```

Admin API actions: `generate`, `submit-for-approval`, `request-edits`, `approve`, `mark-failed`, `archive`, `reject`

## Scheduled Automation (EventBridge)

| Schedule | UTC Time | Function |
|----------|----------|----------|
| Every Monday | 15:15 | `GenerateDraftsFunction` — AI draft generation |
| Every Monday | 00:00 | `NewsletterFunction` — Send weekly newsletter |

## DynamoDB Schema

**Table:** `ContentTable` (pay-per-request)
**Keys:** `pk` (String) / `sk` (String)

| Entity | pk | sk |
|--------|----|----|
| Article | `"ARTICLE"` | article UUID |
| Event | `"EVENT#{articleId}"` | ISO timestamp |
| Subscriber | `"SUBSCRIBER"` | email address |

**GSIs:**
- `StatusUpdatedIndex`: `status` → `updatedAt`
- `StatusPublishedIndex`: `status` → `publishedAt`

## API Endpoints

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET/POST | `/admin` | `admin_api` | List / create articles |
| PATCH | `/admin/articles/{id}` | `admin_api` | Update article status/content |
| POST | `/public/subscribe` | `public_api` | Newsletter subscription |
| POST | `/public/appointment` | `appointment_api` | Appointment request |
| GET | `/site/posts` | `site_data` | List published articles |
| GET | `/site/posts/{id}` | `site_data` | Get single article |

## Common Tasks

**Add a new article status:** Update `src/statuses.py` and add handling in `src/admin_api.py` and `src/db.py`.

**Modify newsletter content:** Edit `src/newsletter.py` — the `newsletter_handler` function builds and sends the email.

**Change draft generation prompts:** Edit `src/automation.py` — the `generate_drafts_handler` calls `src/gemini_client.py`.

**Add a new Lambda function:** Define it in `template.yaml` under `Resources`, then create the handler in `src/`.
