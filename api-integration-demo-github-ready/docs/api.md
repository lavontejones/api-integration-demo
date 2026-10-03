# API reference

Use `Authorization: Bearer <local token>` for all endpoints except `/health`. Send `Content-Type: application/json` with a POST body. The local token comes from `DEMO_API_TOKEN`.

| Method and path | Result |
| --- | --- |
| `GET /health` | Local process status |
| `POST /v1/requests` | Validates and stores one request; returns CRM and notification records |
| `GET /v1/requests/{record_id}` | CRM record and event states |
| `GET /v1/requests/{record_id}/crm` | CRM-ready record |
| `GET /v1/requests/{record_id}/audit` | Ordered audit history |
| `GET /v1/requests/{record_id}/attempts` | Outbound webhook attempts |

POST status codes: `201` stored, `400` invalid JSON, `401` missing or wrong token, `409` exact duplicate, `413` invalid body size, `415` wrong content type, `422` invalid fields, `503` storage unavailable. A webhook failure does not discard a stored request. The `201` body reports `webhook_state: failed` when retries end.

An uncertain customer match creates a separate request with `status: needs_review` and a reason in `duplicate_review`. It does not merge customer records.

The mock receiver supports `/webhook/success`, `/webhook/flaky`, `/webhook/fail`, `/webhook/reject`, `/webhook/redirect`, `/webhook/timeout`, and `/webhook/malformed`. `flaky` fails twice for each event and then succeeds. `reject` returns HTTP 400 and is not retried. `redirect` returns HTTP 302; the sender does not follow redirects. Use `DEMO_WEBHOOK_URL` to select a route before starting the API.
