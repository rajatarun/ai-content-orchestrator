# Architecture — TaskWeave Content Orchestrator

## Table of Contents

1. [System Overview](#system-overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Component Breakdown](#component-breakdown)
4. [Data Flows](#data-flows)
5. [Article Lifecycle State Machine](#article-lifecycle-state-machine)
6. [AI Draft Generation Pipeline](#ai-draft-generation-pipeline)
7. [Newsletter Pipeline](#newsletter-pipeline)
8. [Database Design](#database-design)
9. [API Design](#api-design)
10. [CI/CD Pipeline](#cicd-pipeline)
11. [Security Model](#security-model)
12. [Infrastructure Stack Dependencies](#infrastructure-stack-dependencies)

---

## System Overview

TaskWeave Content Orchestrator is a fully serverless, event-driven content publishing platform built on AWS. It automates the entire lifecycle of LinkedIn content — from creation through AI-powered draft generation, human approval, S3 publishing, and weekly newsletter delivery.

The system is split across two CloudFormation stacks:

| Stack | Role |
|-------|------|
| `tarun-content-team` | Shared infrastructure: artifact S3 bucket, Step Functions state machine, HTTP API for AI teams |
| `tarun-admin-content` *(this repo)* | Content orchestration: admin/public APIs, automation, newsletter, DynamoDB, SNS |

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        External Actors                           │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐                 │
│  │  Admin   │   │  Public  │   │  EventBridge │                 │
│  │  User    │   │  Website │   │  (Scheduler) │                 │
│  └────┬─────┘   └────┬─────┘   └──────┬───────┘                 │
└───────┼──────────────┼───────────────┼──────────────────────────┘
        │              │               │
        ▼              ▼               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    AWS API Gateway (REST)                         │
│         /admin/*        /public/*      /site/*                   │
└──────┬──────────────────────┬──────────────────────┬─────────────┘
       │                      │                      │
       ▼                      ▼                      ▼
┌─────────────┐    ┌──────────────────┐   ┌──────────────────┐
│  AdminApi   │    │  PublicApi /      │   │   SiteData       │
│  Lambda     │    │  AppointmentApi  │   │   Lambda         │
└──────┬──────┘    └──────────────────┘   └────────┬─────────┘
       │                                            │
       ├── invoke ──▶ GenerateDrafts Lambda         │
       │                    │                       │
       │                    ├──▶ TeamWeave API ──▶ Step Functions
       │                    │        (external)
       │                    └──▶ Gemini API (fallback)
       │
       ├── invoke ──▶ Newsletter Lambda
       │                    │
       │                    ├──▶ Gemini API
       │                    └──▶ SES (send to subscribers)
       │
       ▼
┌─────────────────────────┐
│     DynamoDB Table      │
│   (ContentTable)        │
│  Articles / Events /    │
│  Subscribers            │
└─────────────────────────┘

       │ (on approve)
       ▼
┌─────────────────────────┐
│       S3 Bucket         │
│  docs/articles/{id}.json│
└─────────────────────────┘

       │ (after generation)
       ▼
┌─────────────────────────┐
│      SNS Topic          │
│ Approval Notifications  │
│  → email to reviewer    │
└─────────────────────────┘
```

---

## Component Breakdown

### Lambda Functions

| Function | Handler | Trigger | Responsibilities |
|----------|---------|---------|-----------------|
| `AdminApiFunction` | `admin_api.lambda_handler` | API Gateway `/admin/*` | CRUD for articles, trigger generation, approve/publish, subscriber management, newsletter dispatch |
| `PublicApiFunction` | `public_api.lambda_handler` | API Gateway `/public/subscribe` | Newsletter subscription, SES contact list management |
| `AppointmentApiFunction` | `appointment_api.lambda_handler` | API Gateway `/public/appointment` | Appointment request intake, SES notification |
| `SiteDataFunction` | `site_data.lambda_handler` | API Gateway `/site/posts` | Serves published articles from S3 |
| `GenerateDraftsFunction` | `automation.generate_drafts_handler` | EventBridge (Mon 15:15 UTC) + Admin invoke | AI draft generation via TeamWeave or Gemini fallback |
| `NewsletterFunction` | `newsletter.newsletter_handler` | EventBridge (Mon 00:00 UTC) + Admin invoke | Newsletter content generation and SES delivery |

### Data Stores

| Store | Service | Purpose |
|-------|---------|---------|
| `ContentTable` | DynamoDB | Articles, audit events, subscribers |
| Artifact bucket | S3 (`tarun-rag-docs-*`) | Published article JSON files |
| Config/artifact bucket | S3 (shared stack) | TeamWeave run artifacts (`runs/{runId}/writer_linkedin.json`) |
| Gemini API key | Secrets Manager | Secure API key storage |

### Messaging & Eventing

| Resource | Type | Purpose |
|----------|------|---------|
| `ApprovalNotificationTopic` | SNS | Email alert when draft is ready for approval |
| `WeeklyGenerateDraftsRule` | EventBridge | Triggers draft generation every Monday at 15:15 UTC |
| `WeeklyNewsletterRule` | EventBridge | Triggers newsletter send every Monday at 00:00 UTC |

---

## Data Flows

### 1. Article Creation & Draft Generation

```
Admin User
  │
  ├─POST /admin/articles ──▶ AdminApi Lambda
  │                                │
  │                                ▼
  │                          DynamoDB.put_article()
  │                          (status = DRAFT)
  │
  ├─POST /admin/articles/{id}/actions/generate ──▶ AdminApi Lambda
  │                                                      │
  │                                        invoke async  ▼
  │                                          GenerateDrafts Lambda
  │                                                │
  │                                    ┌───────────┴────────────┐
  │                                    ▼                        ▼
  │                             TeamWeave API            (on failure)
  │                             POST /team/task          Gemini API
  │                             (SigV4 signed)           (BAU fallback)
  │                                    │
  │                             poll Step Functions
  │                             DescribeExecution
  │                                    │
  │                             fetch S3 artifact
  │                             runs/{runId}/writer_linkedin.json
  │                                    │
  │                             DynamoDB.update_article()
  │                             (status = AWAITING_APPROVAL)
  │                                    │
  │                             SNS.publish()
  │                             → approval email
```

### 2. Article Approval & Publishing

```
Admin User
  │
  ├─POST /admin/articles/{id}/actions/approve ──▶ AdminApi Lambda
  │                                                      │
  │                                           ┌──────────┴─────────┐
  │                                           ▼                    ▼
  │                                    DynamoDB.update()     S3.put_object()
  │                                    status=APPROVED       docs/articles/{id}.json
  │                                           │
  │                                    DynamoDB.add_event()
  │                                    "PUBLISHED_TO_S3"
```

### 3. Weekly Newsletter

```
EventBridge (Monday 00:00 UTC)
  │
  ▼
Newsletter Lambda
  │
  ├─── DynamoDB: query PUBLISHED articles (last 7 days)
  │
  ├─── Gemini API: generate newsletter content
  │    (subject, body_text, body_html)
  │
  └─── SES: send to all ACTIVE subscribers
       (batched via send_email helper)
```

---

## Article Lifecycle State Machine

```
                    ┌─────────┐
                    │  DRAFT  │◀────────────────────────┐
                    └────┬────┘                         │
                         │                              │
           ┌─────────────┴─────────────┐               │
           │ generate / submit         │ request-edits  │
           ▼                           │               │
   ┌───────────────────┐               │               │
   │  AWAITING_APPROVAL│───────────────┘               │
   └────────┬──────────┘                               │
            │                                          │
     ┌──────┴──────┐                                   │
     │ approve     │ request-edits                     │
     ▼             ▼                                   │
 ┌────────┐  ┌──────────────────┐                      │
 │APPROVED│  │REVISION_REQUESTED│──── submit ──────────┘
 └───┬────┘  └──────────────────┘
     │
     ├── mark-published
     ▼
┌──────────┐
│ PUBLISHED│
└────┬─────┘
     │
     │ archive (any state)
     ▼
┌──────────┐      ┌────────┐
│ ARCHIVED │      │ FAILED │◀── reject / mark-failed
└──────────┘      └────────┘
```

**Available Actions per Status:**

| Current Status | Available Actions |
|---------------|-------------------|
| `DRAFT` | `generate`, `submit-for-approval`, `request-edits`, `reject` |
| `REVISION_REQUESTED` | `submit-for-approval`, `reject` |
| `AWAITING_APPROVAL` | `approve`, `request-edits`, `reject` |
| `APPROVED` | `mark-published`, `archive` |
| `PUBLISHED` | `archive` |
| `FAILED` | `submit-for-approval`, `archive` |
| `ARCHIVED` | *(none)* |

---

## AI Draft Generation Pipeline

The generation system uses a two-tier approach with automatic fallback:

### Primary Path — TeamWeave

```
GenerateDrafts Lambda
  │
  ├─ 1. POST /team/task  (SigV4-signed HTTP to shared stack API)
  │       payload: { team, version, request: { topic, channel, audience, objective } }
  │
  ├─ 2. Receive: { run_id, execution_arn }
  │
  ├─ 3. Poll Step Functions DescribeExecution
  │       - interval: 1s → 5s (linear backoff)
  │       - max wait: 90s (configurable via MAX_POLL_SECONDS)
  │       - terminal states: SUCCEEDED / FAILED / TIMED_OUT / ABORTED
  │
  ├─ 4. Fetch S3 artifact:
  │       s3://{ARTIFACT_BUCKET}/runs/{runId}/writer_linkedin.json
  │       Shape: { drafts: [ { variant, linkedin_post, hashtags, cta_question } ] }
  │
  └─ 5. Persist result to DynamoDB, publish SNS notification
```

### Fallback Path — BAU (Gemini Direct)

Triggered when TeamWeave API fails or execution doesn't SUCCEED:

```
  ├─ 1. Build structured prompt:
  │       { task, instructions, context: { topic, objective, channel, audience }, schema }
  │
  ├─ 2. POST to Gemini API
  │       model: gemini-3-flash-preview (configurable)
  │       google_search tool: disabled for BAU
  │
  └─ 3. Parse JSON response → normalize drafts → persist
```

---

## Newsletter Pipeline

```
newsletter_handler (action="generate")
  ├─ Query DynamoDB: PUBLISHED articles between date_from and date_to (last 7 days)
  ├─ Build Gemini prompt with article titles + URLs
  └─ Return: { subject, body_text, body_html }

newsletter_handler (action="send")
  ├─ Fetch all ACTIVE subscribers from DynamoDB
  ├─ Validate SES_FROM_EMAIL
  └─ SES SendEmail to all subscribers
```

The two-step design allows admins to preview and edit the generated newsletter before sending.

---

## Database Design

### Table: `ContentTable` (DynamoDB, PAY_PER_REQUEST)

**Primary Key:** `pk` (HASH) + `sk` (RANGE)

#### Entity Patterns

**Article**
```json
{
  "pk": "ARTICLE",
  "sk": "<uuid>",
  "id": "<uuid>",
  "entityType": "ARTICLE",
  "title": "...",
  "sourceInputs": "...",
  "tags": [],
  "status": "DRAFT | AWAITING_APPROVAL | ...",
  "createdAt": "2026-01-01T00:00:00Z",
  "updatedAt": "2026-01-01T00:00:00Z",
  "publishedAt": "2026-01-07T00:00:00Z",
  "publishedUrl": "https://...",
  "revisionNote": "...",
  "generated": {
    "source": "teamweave | bau",
    "status": "SUCCEEDED | FALLBACK",
    "drafts": [ { "variant": "...", "linkedin_post": "...", "hashtags": [], "cta_question": "..." } ],
    "linkedin_post": "...",
    "hashtags": [],
    "cta_question": "..."
  },
  "meta": { "version": 3, "retryCount": 0, "lastError": "" }
}
```

**Audit Event**
```json
{
  "pk": "EVENT#<articleId>",
  "sk": "<ISO timestamp>",
  "entityType": "EVENT",
  "articleId": "<uuid>",
  "type": "CREATED | GENERATED | PUBLISHED_TO_S3 | ...",
  "message": "..."
}
```

**Subscriber**
```json
{
  "pk": "SUBSCRIBER",
  "sk": "<email>",
  "entityType": "SUBSCRIBER",
  "email": "<email>",
  "status": "ACTIVE",
  "createdAt": "..."
}
```

#### Global Secondary Indexes

| Index | Hash Key | Range Key | Used By |
|-------|----------|-----------|---------|
| `StatusUpdatedIndex` | `status` | `updatedAt` | List articles by status (admin list view) |
| `StatusPublishedIndex` | `status` | `publishedAt` | Query published articles for newsletter |

---

## API Design

### REST API (API Gateway)

Base URL: `https://{restApiId}.execute-api.us-east-1.amazonaws.com/prod`

#### Admin Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin` | Health check |
| `POST` | `/admin/articles` | Create article |
| `GET` | `/admin/articles?status=DRAFT&limit=20` | List articles by status |
| `GET` | `/admin/articles/{id}` | Get article with available CTAs |
| `PATCH` | `/admin/articles/{id}` | Update article fields |
| `GET` | `/admin/articles/{id}/events` | Get audit trail |
| `POST` | `/admin/articles/{id}/actions/generate` | Trigger async AI generation |
| `POST` | `/admin/articles/{id}/actions/approve` | Approve + publish to S3 |
| `POST` | `/admin/articles/{id}/actions/submit-for-approval` | Submit for review |
| `POST` | `/admin/articles/{id}/actions/request-edits` | Request revision |
| `POST` | `/admin/articles/{id}/actions/reject` | Reject → FAILED |
| `POST` | `/admin/articles/{id}/actions/mark-failed` | Mark as failed |
| `POST` | `/admin/articles/{id}/actions/archive` | Archive article |
| `POST` | `/admin/articles/{id}/actions/mark-published` | Mark as published |
| `POST` | `/admin/newsletter/actions/generate` | Generate newsletter preview |
| `POST` | `/admin/newsletter/actions/send` | Send newsletter to subscribers |
| `GET` | `/admin/subscribers` | List all subscribers |
| `POST` | `/admin/subscribers` | Add subscriber |
| `DELETE` | `/admin/subscribers/{email}` | Remove subscriber |

#### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/public/subscribe` | Subscribe to newsletter |
| `POST` | `/public/appointment` | Request an appointment |
| `GET` | `/site/posts` | List published articles (from S3) |
| `GET` | `/site/posts/{id}` | Get single published article |

All endpoints support `OPTIONS` for CORS preflight with `*` origin.

---

## CI/CD Pipeline

```
Developer pushes to main
        │
        ▼
GitHub Actions: deploy.yml
        │
        ├─ 1. Checkout code
        ├─ 2. Configure AWS credentials (OIDC — no long-lived keys)
        │       role: teamweave-github-actions-sam-deployer
        │
        ├─ 3. Setup Python 3.11 + SAM CLI
        ├─ 4. pip install -r requirements.txt
        ├─ 5. sam build --template-file template.yaml
        ├─ 6. sam validate (lint)
        │
        ├─ 7. Load shared stack outputs from tarun-content-team
        │       ArtifactBucket, StatusFunctionArn, HttpApiUrl
        │
        └─ 8. sam deploy
                --stack-name tarun-admin-content
                --resolve-s3   (auto-managed SAM deployment bucket)
                --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM
```

**Environment Variables (GitHub)**:

| Variable | Default | Purpose |
|----------|---------|---------|
| `REGION` | `us-east-1` | AWS deployment region |
| `STACK_NAME` | `tarun-admin-content` | CloudFormation stack name |
| `TEAM_STACK_NAME` | `tarun-content-team` | Shared stack for cross-stack outputs |

---

## Security Model

| Concern | Implementation |
|---------|---------------|
| **API Authentication** | Admin API relies on network/client-level access control (no built-in auth; intended for trusted admin clients) |
| **AI Key Storage** | Gemini API key stored in AWS Secrets Manager; never in environment variables directly |
| **CI/CD Credentials** | GitHub Actions uses OIDC (no long-lived AWS keys); assumes a least-privilege IAM role |
| **Lambda IAM** | Each function has a scoped IAM policy (DynamoDB, S3, SES, SNS, Secrets Manager only what's needed) |
| **Outbound Signing** | TeamWeave API calls are SigV4-signed using Lambda execution role credentials |
| **CORS** | All public endpoints allow `*` origin; admin should be restricted by deploy configuration |
| **Data at Rest** | DynamoDB and S3 use default AWS encryption at rest |
| **Secrets Rotation** | Gemini API key can be rotated in Secrets Manager; Lambda picks up new value on cold start |

---

## Infrastructure Stack Dependencies

```
tarun-content-team (shared stack)         tarun-admin-content (this repo)
┌─────────────────────────────┐           ┌──────────────────────────────┐
│ ArtifactBucket  ────────────┼──────────▶│ GenerateDraftsFunction       │
│ StatusFunctionArn ──────────┼──────────▶│ AdminApiFunction             │
│ HttpApiUrl ─────────────────┼──────────▶│ GenerateDraftsFunction       │
│ Step Functions state machine│           │ (TeamWeave API URL)          │
│ Writer Lambda               │           │                              │
└─────────────────────────────┘           └──────────────────────────────┘
```

The `tarun-content-team` stack must be deployed first. Its CloudFormation outputs are read during the `sam deploy` step of this stack's CI/CD pipeline.
