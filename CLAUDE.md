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
openapi/              # OpenAPI 3.0 description of the HTTP surface
tests/                # Contract tests (spec vs handlers vs template)
scripts/stack_env.py  # Resolve API/table/bucket coordinates from stack outputs
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

Contract tests run with no AWS and no deployed stack:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

They check the OpenAPI spec against the handlers' route literals and against
`template.yaml` (routing, GSI names, the Outputs `scripts/stack_env.py` reads).
CI runs them before `sam build`, so drift stops the deploy. There is still no
behavioural test suite for the handlers themselves — those can be exercised by
passing JSON event payloads directly.

## Key Configuration

SAM template parameters (set at deploy time or in CI secrets):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `GeminiApiKeySecretName` | `gemini/api_key` | Secrets Manager key for Gemini API |
| `GeminiModel` | `gemini-3.8-flash` | Gemini model to use |
| `SesFromEmail` | `rajatarun12@gmail.com` | Verified SES sender address |
| `ArtifactBucket` | (shared stack) | S3 bucket for run artifacts and team config |
| `ArticlesBucket` | `tarun-rag-docs-…` | S3 bucket `/site/posts` reads published articles from |
| `ArticlesPrefix` | `docs/articles` | Key prefix under `ArticlesBucket` |

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

`openapi/content-orchestrator.yaml` is the full description, including request
and response shapes. `tests/test_openapi_contract.py` compares it against the
route literals in `src/*.py` in both directions, so unlike the table below it
cannot drift unnoticed — this table is a summary, that file is the reference.

**Admin** (`admin_api`) — requires both an API key (`x-api-key`) and a SIWE
bearer JWT:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin` | Liveness probe — *not* a list of articles |
| GET/POST | `/admin/articles` | List by status (GSI-backed) / create a draft |
| GET/PATCH | `/admin/articles/{id}` | Read / merge fields into one article |
| GET | `/admin/articles/{id}/events` | Audit trail |
| POST | `/admin/articles/{id}/actions/{action}` | Every lifecycle transition |
| POST | `/admin/newsletter/actions/generate` | Build the newsletter, do not send |
| POST | `/admin/newsletter/actions/send` | Build and send via SES — no dry run |
| GET/POST | `/admin/subscribers` | List / add |
| DELETE | `/admin/subscribers/{email}` | Remove (idempotent) |

**Public** (`public_api`, `appointment_api`, `site_data`) — no credentials:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/public/subscribe` | Newsletter subscription |
| POST | `/public/appointment` | Appointment request |
| GET | `/site/posts` | Published article cards, read from S3 |
| GET | `/site/posts/{id}` | One article as stored in S3 (a snapshot, not the live item) |

Status transitions go through the actions endpoint, not PATCH: PATCHing
`status` directly skips the S3 publish and the event-log write that `approve`
performs.

## Stack Outputs

Everything an automation harness needs is published by the stack; nothing needs
to be hardcoded. `scripts/stack_env.py` resolves them into environment
variables:

```bash
eval "$(python scripts/stack_env.py --stack tarun-admin-content --format sh)"
curl -sS "$ACO_API_BASE/site/posts"
```

| Output | Why a harness needs it |
|--------|------------------------|
| `ApiBaseUrl` | The server for every request |
| `ContentTableName` | The DynamoDB table |
| `ContentTableStatusUpdatedIndex` / `...PublishedIndex` | The GSI names — a table name alone does not let you query a table whose access pattern is an index |
| `ArticlesBucketName` / `ArticlesPrefix` | Where `/site/posts` reads from |
| `GeminiModelId` | The model the stack is *actually* deployed with (see below) |
| `OpenApiSpecPath` | Where the spec lives in the repo |

`GeminiModelId` is published because `sam deploy` sends
`UsePreviousValue=true` for any parameter absent from `--parameter-overrides`:
the deployed value can differ from the template default with nothing in the
repository showing it, which is exactly how the admin API came to serve a stale
model id.

## Common Tasks

**Add a new article status:** Update `src/statuses.py` and add handling in `src/admin_api.py` and `src/db.py`.

**Modify newsletter content:** Edit `src/newsletter.py` — the `newsletter_handler` function builds and sends the email.

**Change draft generation prompts:** Edit `src/automation.py` — the `generate_drafts_handler` calls `src/gemini_client.py`.

**Add a new Lambda function:** Define it in `template.yaml` under `Resources`, then create the handler in `src/`.
