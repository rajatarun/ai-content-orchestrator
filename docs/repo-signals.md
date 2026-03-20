# Repo Signals — ai-content-orchestrator

AWS serverless application automating a weekly content publishing pipeline with AI-powered draft generation, newsletter delivery, and article lifecycle management.

## Tech Stack

| Category | Value |
|---|---|
| **Runtime** | Python 3.11 |
| **IaC** | AWS SAM |
| **AWS Services** | Lambda, DynamoDB, S3, SES, SNS, EventBridge, API Gateway, Secrets Manager |
| **External APIs** | Google Gemini, TeamWeave HTTP API |
| **CI/CD** | GitHub Actions (OIDC) |

## Design Patterns

### Architectural

#### Serverless / Lambda Handler
**Files:** `src/admin_api.py`, `src/public_api.py`, `src/site_data.py`, `src/appointment_api.py`, `src/automation.py`, `src/newsletter.py`

Six Lambda functions with standardized `(event, context)` handler signatures serve as the core compute units. API Gateway routes HTTP requests; EventBridge triggers scheduled handlers.

#### Event-Driven Architecture
**Files:** `template.yaml`, `src/automation.py`, `src/newsletter.py`

EventBridge cron rules fire `GenerateDraftsFunction` (Monday 15:15 UTC) and `NewsletterFunction` (Monday 00:00 UTC) asynchronously, removing temporal coupling from API call paths.

#### Thin Controller
**Files:** `src/admin_api.py`, `src/public_api.py`, `src/site_data.py`, `src/appointment_api.py`

Lambda handlers focus on request parsing, input validation, and response formatting. Domain logic is delegated to service modules (`db.py`, `gemini_client.py`, `mailer.py`, `publisher.py`), keeping controllers thin.

#### API Gateway Facade
**Files:** `template.yaml`

A single API Gateway instance with API-key authentication, Lambda authorizer (SIWE), usage throttling, and request logging acts as a unified facade in front of multiple independent Lambda functions.

---

### Behavioral

#### State Machine
**Files:** `src/statuses.py`, `src/admin_api.py`

Article lifecycle (`DRAFT → AWAITING_APPROVAL → APPROVED → PUBLISHED`) is modelled as an explicit state machine. `_decorate_article_with_actions()` maps the current state to valid next actions and enforces transition guards.

#### Strategy Pattern (with Fallback)
**Files:** `src/automation.py`

Draft generation tries the TeamWeave external service first (primary strategy). On failure it automatically falls back to direct Gemini BAU generation (secondary strategy), providing transparent resilience.

#### Null Object / Safe Default
**Files:** `src/admin_api.py`, `src/public_api.py`, `src/automation.py`

Handlers return empty dicts `{}` instead of `None` for missing bodies/parameters, and use `or {}` guards throughout, eliminating null-check boilerplate in downstream code.

---

### Structural

#### Adapter Pattern
**Files:** `src/admin_api.py`, `src/site_data.py`, `src/public_api.py`

`_qs()` and path extraction helpers normalise events from both REST API v1 and HTTP API v2 formats into a uniform internal representation, decoupling handlers from API Gateway version specifics.

#### Decorator Pattern
**Files:** `src/admin_api.py`, `src/logger.py`

`_decorate_article_with_actions()` enriches article dicts with derived CTA data without mutating the original. `ExtraFieldsFormatter` decorates log records with extra JSON context fields.

#### JSON Serialization Adapter
**Files:** `src/admin_api.py`, `src/publisher.py`

`_json_safe()` recursively converts DynamoDB `Decimal` values to `int`/`float` before JSON serialisation, adapting the DynamoDB type system to the JSON type system without polluting the repository layer.

---

### Creational

#### Builder Pattern
**Files:** `src/automation.py`, `src/mailer.py`

`_build_bau_prompt()` constructs complex nested JSON prompt payloads step-by-step. `MIMEMultipart` in `mailer.py` assembles MIME email messages with headers and body parts incrementally.

#### Singleton Logger
**Files:** `src/logger.py`

`get_logger()` checks `logger.handlers` before adding a new handler, ensuring the logger is initialised only once per Lambda container lifetime regardless of how many modules import it.

---

### Data Access

#### Repository Pattern
**Files:** `src/db.py`

All DynamoDB operations are centralized in `db.py`, exposing a clean API (`put_article`, `get_article`, `list_by_status`, `add_event`, `put_subscriber`, etc.) that hides persistence details from handlers.

#### Pagination (Token-Based)
**Files:** `src/site_data.py`, `src/automation.py`

S3 object listings use AWS paginators and `ContinuationToken`-based iteration to handle collections that exceed a single API response page, ensuring all objects are processed regardless of scale.

---

### Data

#### Audit Log / Event Sourcing
**Files:** `src/db.py`, `src/admin_api.py`

`add_event()` appends immutable `EVENT#{articleId}` records with ISO-timestamp sort keys for every significant state change (`CREATED`, `GENERATED`, `PUBLISHED_TO_S3`, `GENERATE_FAILED`), providing a full audit trail.

---

### Messaging

#### Pub/Sub (SNS)
**Files:** `src/automation.py`, `template.yaml`

An SNS topic (`ApprovalNotificationTopic`) decouples approval-state transitions from email notification delivery, allowing additional subscribers to be added without changing producer code.

---

### Concurrency

#### Asynchronous Fire-and-Forget
**Files:** `src/admin_api.py`

Draft generation is triggered via Lambda `InvocationType="Event"` (async), returning an immediate API response while the generation runs independently in the background.

#### Parallel Execution (ThreadPoolExecutor)
**Files:** `src/site_data.py`

Blog-card data is fetched from S3 concurrently using `ThreadPoolExecutor`, with configurable parallelism (default 5, max 10), reducing list-endpoint latency proportionally.

---

### Resilience

#### Polling with Exponential Backoff
**Files:** `src/automation.py`

`pollExecution()` polls the TeamWeave step-function execution until a terminal state is reached, using an increasing interval (up to 5 s) bounded by `MAX_POLL_SECONDS` to avoid thundering-herd retries.

#### Graceful Degradation
**Files:** `src/public_api.py`

Non-critical side-effects (e.g. SES contact-list registration) are wrapped in `try/except` with a `pass` fallback, so a failure in an optional integration never blocks the primary subscription flow.

---

### Security

#### Secrets Manager Pattern
**Files:** `src/aws_secrets.py`, `src/gemini_client.py`

Credentials (Gemini API key, JWT signing secret) are retrieved at runtime from AWS Secrets Manager rather than environment variables, keeping sensitive values out of Lambda configuration.

#### SigV4 Request Signing
**Files:** `src/automation.py`

Outbound HTTP requests to internal AWS-hosted APIs are signed with AWS Signature Version 4 via boto3's `SigV4Auth`, ensuring requests are authenticated without embedding long-lived credentials.

#### JWT Token Signing
**Files:** `src/automation.py`

Short-lived HS256 JWT tokens are created in `_create_jwt()` using a secret from Secrets Manager to authorize TeamWeave task API calls, avoiding static API keys.

---

### Configuration

#### Environment-Based Configuration
**Files:** `template.yaml`, `src/admin_api.py`, `src/automation.py`, `src/newsletter.py`

All tuneable values (`TABLE_NAME`, `GEMINI_MODEL`, `ARTICLES_BUCKET`, `SES_FROM_EMAIL`, `ALLOWED_ORIGIN`, `MAX_POLL_SECONDS`) are injected as Lambda environment variables via SAM parameter overrides, enabling per-environment configuration without code changes.
