# Northstar API Integration Demo

A working, local reference for a small-business intake system. It accepts a request, checks the data, finds possible duplicates, assigns a team, stores the work, prepares a customer relationship management (CRM) record, sends a local webhook, creates a dashboard notification, and records an audit trail.

**All companies, people, contact details, transactions, and values are synthetic.** Northstar Commercial Services LLC is fictional. The project does not show a client result or claim a live business integration.

## Business problem and scenario

A service business can receive requests by website, phone, referral, and email. If each channel uses a different format, staff may miss a request or create repeated customer records. Northstar is a fictional commercial facilities service company with approximately 34 employees and $4.8 million in annual revenue. These figures are illustrative. The demo gives its team one intake process and a visible record of each decision.

## Architecture and data flow

```mermaid
flowchart LR
  A[Inbound request] --> B[REST API]
  B --> C[Validate and normalize]
  C --> D[Duplicate check and routing]
  D --> E[(SQLite)]
  E --> F[CRM-ready record]
  F --> G[Local webhook receiver]
  F --> H[Dashboard notification]
  G --> I[Attempts and audit history]
  H --> I
```

The API uses JSON over HTTP. SQLite stores customers, requests, events, webhook attempts, and audit records. A **webhook** is an HTTP message sent when an event occurs. The receiver in this project runs on the same computer. See [architecture and decision rules](docs/architecture.md) and the [API reference](docs/api.md).

## Features

- Required-field and format checks with clear JSON errors.
- Normalized names, email, phone, categories, currency, and timestamps.
- Exact duplicate rejection for `request_id` and `external_reference`.
- Human-review status for uncertain customer matches and repeated request types within 24 hours. No silent merge.
- Readable, ordered team assignment rules in `src/northstar/rules.py`.
- Persistent SQLite records and an ordered audit trail.
- CRM-ready JSON and a stored internal dashboard notification.
- Local webhook success, failure, timeout, malformed response, and retry paths.
- Bounded retries with an attempt record for each send.
- Bearer-token pattern, environment settings, structured logs, tests, and GitHub Actions.

## Repository structure

```text
.
├── .env.example
├── .github/workflows/validate.yml
├── docs/                 # Design and API reference
├── samples/              # Synthetic valid and invalid requests
├── scripts/smoke_demo.py # Starts local servers and shows key outcomes
├── src/northstar/        # API, rules, storage, webhook, receiver
└── tests/                # Unit and real local HTTP tests
```

## Setup

Use Python 3.9 or newer. The runtime uses only Python's standard library. No account, paid API, or package install is needed.

```bash
cp .env.example .env
```

The `.env` file is local and ignored by Git. Its token is an example value for this local demo. Set `DEMO_API_TOKEN` to a different value for your own test. Environment variables override `.env` values.

## Run locally

Open two terminals from the repository root:

```bash
# Terminal 1: local webhook receiver
PYTHONPATH=src python3 -m northstar.mock_receiver
```

```bash
# Terminal 2: intake API
PYTHONPATH=src python3 -m northstar.api
```

The API listens on `127.0.0.1:8000`. The receiver listens on `127.0.0.1:8001`. The database is created at `work/demo.sqlite3`. Check `http://127.0.0.1:8000/health` in a browser. Stop each process with Ctrl+C.

For one command that starts both servers, sends requests, and removes its temporary database:

```bash
PYTHONPATH=src python3 scripts/smoke_demo.py
```

## Example API request

The first object in [samples/requests.json](samples/requests.json) is a complete example. The phone number is in the reserved fictional `555-01xx` range and the email uses `example.com`.

```bash
curl -X POST http://127.0.0.1:8000/v1/requests \
  -H 'Authorization: Bearer example-local-token-change-me' \
  -H 'Content-Type: application/json' \
  --data-binary @samples/request_oak_ridge.json
```

## Example response and CRM output

IDs and creation times change on each run. This shortened example shows the main fields. The real response also includes the complete `crm_record`, `notification`, and links to the audit and attempt records.

```json
{
  "record_id": "req_<generated-id>",
  "status": "new",
  "assigned_team": "Senior Sales",
  "duplicate_review": null,
  "webhook_state": "delivered",
  "crm_record": {
    "company": "Oak Ridge Dental Group",
    "request_type": "sales",
    "estimated_value": "35000.00",
    "currency": "USD",
    "priority": "normal",
    "assigned_team": "Senior Sales",
    "status": "new",
    "tags": ["sales", "normal", "website"]
  },
  "notification": {
    "channel": "internal_dashboard",
    "recipient_team": "Senior Sales",
    "kind": "new_request"
  }
}
```

The CRM record also contains the normalized contact, location, source, service interest, summary, and timestamps. It is ready for an adapter to map into a real CRM. This demo does not connect to a live CRM.

## Example webhook event

```json
{
  "event_id": "evt_<generated-id>",
  "event_type": "crm.request.created",
  "schema_version": 1,
  "record": {
    "record_id": "req_<generated-id>",
    "company": "Oak Ridge Dental Group",
    "assigned_team": "Senior Sales",
    "status": "new"
  }
}
```

This is an abbreviated view of the stored event. The local receiver checks the event ID and returns a JSON acknowledgement.

## Failure and retry example

Change `DEMO_WEBHOOK_URL` in `.env` to `http://127.0.0.1:8001/webhook/flaky`. Restart the API. Send a new request ID. The receiver returns HTTP 503 twice, then succeeds. The attempts endpoint shows three rows: failure, failure, success. Use `/webhook/fail` to see three failures and `webhook_state: failed`. A failed webhook leaves the accepted request in SQLite for inspection. The sample script runs both paths.

An invalid request returns HTTP 422 with a list such as `{"field":"email","message":"Enter a valid email address"}`. An exact duplicate returns HTTP 409 with the existing record ID. Examples are in [samples/invalid_requests.json](samples/invalid_requests.json).

## Synthetic sample business data

The six valid records represent Oak Ridge Dental Group, Summit Property Management, Harborview Fitness, Bluebird Logistics, Meridian Accounting Partners, and Evergreen Medical Offices. Values range from $750 to $85,000 and cover sales, service, billing, and general requests. One repeated submission is created during tests to show duplicate handling. All figures are illustrative.

## Security approach

The API requires a local bearer token for request and record endpoints. The token is read from an environment variable or a local `.env` file. The API compares it without printing it. JSON bodies have a size limit. The webhook URL must point to `localhost` or `127.0.0.1` when loaded from configuration. Logs use action names and internal IDs; they do not print payloads or tokens. `.gitignore` excludes the local token file and database. Read [SECURITY.md](SECURITY.md) before adapting the code.

## Testing and CI

Run the deterministic tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests cover validation, normalization, routing, token checks, SQLite writes, exact and uncertain duplicates, CRM output, notification output, audit records, webhook success, timeouts, malformed responses, retry success, and final failure. HTTP tests use local ports and a temporary database.

The GitHub Actions workflow runs compilation and tests on each push and pull request with Python 3.9 and 3.12. A failing test fails the workflow. Local tests can be run before publication; a GitHub Actions result exists only after a push to GitHub.

## Limits and commercial adaptation

This is a functional reference, not a production service. It has no live CRM, email, SMS, booking, or accounting credentials. It sends the webhook while the intake request waits. It has no durable background worker, TLS, rate limits, secret manager, retention policy, or signed webhook. These controls would need design work before real data or public deployment.

The same pattern can support website lead intake, CRM handoffs, booking systems, service requests, onboarding, support intake, billing workflows, internal operations, field-service routing, sales pipelines, and accounting handoffs. Each adaptation would map fields and rules to the real system, add its authentication and privacy controls, and test with approved data. No customer outcome or return on investment is claimed here.
