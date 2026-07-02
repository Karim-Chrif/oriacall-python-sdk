# oriacall

Python SDK for the Oriacall Developer API.

## Install

```bash
pip install oriacall
```

Requirements:

- Python 3.9 or newer.
- An Oriacall Developer API client ID and secret.
- Server-side usage only. Do not expose `client_secret` in browser or client app code.

## Quickstart

```python
import os

from oriacall import Oriacall

oriacall = Oriacall(
    client_id=os.environ["ORIACALL_CLIENT_ID"],
    client_secret=os.environ["ORIACALL_CLIENT_SECRET"],
    scope=["hello:read", "objectives:read", "agents:read", "calls:read", "leads:read"],
)

hello = oriacall.hello.get()
print(hello.data["message"], hello.request_id)

calls = oriacall.calls.list({"limit": 50})
print(calls.data["data"], calls.request_id)
```

The SDK requests and caches a short-lived access token using client credentials, then sends it as a bearer token for API calls.

## Client Options

```python
oriacall = Oriacall(
    base_url="https://api.oriacall.com",
    client_id=os.environ["ORIACALL_CLIENT_ID"],
    client_secret=os.environ["ORIACALL_CLIENT_SECRET"],
    scope=["calls:read"],
    retries=2,
    retry_base_delay_ms=250,
    retry_max_delay_ms=2000,
    timeout_seconds=30,
    on_response=lambda event: print(event),
)
```

Options:

| Option | Type | Required | Description |
| --- | --- | --- | --- |
| `client_id` | `str` | Yes | Developer API client ID. |
| `client_secret` | `str` | Yes | Developer API client secret. Keep this server-side. |
| `base_url` | `str` | No | API base URL. Defaults to `https://api.oriacall.com`. |
| `scope` | `str | list[str] | None` | No | Space-separated string or list of requested token scopes. If omitted, the token request uses all scopes granted to the client. |
| `session` | `requests.Session | None` | No | Custom session for tests or transport customization. |
| `on_response` | `callable | None` | No | Called for every SDK-managed HTTP response, including token requests. |
| `retries` | `int` | No | Retry count for token requests and GET endpoints. Defaults to `0`. |
| `retry_base_delay_ms` | `int` | No | Initial retry delay. Defaults to `250`. |
| `retry_max_delay_ms` | `int` | No | Maximum retry delay. Defaults to `2000`. |
| `timeout_seconds` | `int` | No | HTTP request timeout. Defaults to `30`. |

## Response Envelope

Every endpoint method returns `ApiResponse`:

```python
response.data       # decoded JSON dict, or None for delete responses
response.status     # HTTP status code
response.request_id # X-Request-Id when provided
```

## Methods

```python
oriacall.get_access_token()
oriacall.raw("GET", "/v1/hello")
oriacall.hello.get()

oriacall.objectives.list({"limit": 50})
oriacall.objectives.update("objective-id", {"customFields": {"region": "north"}})
oriacall.objectives.paginate({"limit": 50})

oriacall.objective_custom_fields.list()
oriacall.objective_custom_fields.create({"key": "region", "label": "Region", "type": "text"})
oriacall.objective_custom_fields.update("region", {"label": "Sales Region"})

oriacall.agents.list({"objectiveId": "objective-id"})
oriacall.agents.paginate({"limit": 50})

oriacall.calls.list({"limit": 50, "sortBy": "recordedAt"})
oriacall.calls.get("call-id")
oriacall.calls.upload({...})
oriacall.calls.queue_analysis("call-id")
oriacall.calls.wait_for_analysis("call-id", {"timeout_ms": 120000})
oriacall.calls.paginate({"limit": 50})

oriacall.leads.list({"customFields": {"crm_stage": "qualified"}})
oriacall.leads.get("lead-id")
oriacall.leads.update("lead-id", {"customFields": {"crm_stage": "won"}})
oriacall.leads.upsert_by_external_id("crm-lead-id", {"firstName": "Ada", "lastName": "Lovelace"})
oriacall.leads.paginate({"limit": 50})

oriacall.lead_custom_fields.list()
oriacall.lead_custom_fields.create({"key": "crm_stage", "label": "CRM Stage", "type": "text"})
oriacall.lead_custom_fields.update("crm_stage", {"label": "CRM Stage"})

oriacall.webhooks.endpoints.list()
oriacall.webhooks.endpoints.create({"url": "https://example.com/oriacall/webhooks", "events": ["analysis.completed"]})
oriacall.webhooks.endpoints.update("endpoint-id", {"isActive": False})
oriacall.webhooks.endpoints.rotate_secret("endpoint-id")
oriacall.webhooks.endpoints.test("endpoint-id")
oriacall.webhooks.endpoints.delete("endpoint-id")
oriacall.webhooks.endpoints.paginate({"limit": 50})
```

`oriacall.calls.get("call-id")` includes transcript data when available. Transcript turn `speaker` values can be `agent`, `client`, or `system`. `system` represents telephony infrastructure such as voicemail greetings, carrier messages, transfer prompts, or tones; it is not a human participant.

## Upload A Call

```python
response = oriacall.calls.upload(
    {
        "idempotencyKey": "crm-call-123",
        "externalId": "crm-call-123",
        "recordedAt": "2026-06-10T14:30:00Z",
        "objectiveId": "objective-id",  # optional hint
        "queueAnalysis": True,
        "agent": {
            "externalId": "agent-1",
            "name": "Morgan Agent",
            "email": "morgan@example.com",
        },
        "lead": {
            "externalId": "lead-1",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "phone": "+15555550100",
            "customFields": {"crm_stage": "qualified"},
        },
        "audio": {
            "path": "./call.mp3",
            "filename": "call.mp3",
            "contentType": "audio/mpeg",
        },
    }
)

print(response.data["data"]["id"])
print(response.data["data"]["recordedAt"])
```

To upload in-memory audio, use `contents` instead of `path`:

```python
"audio": {
    "contents": audio_bytes,
    "filename": "call.mp3",
    "contentType": "audio/mpeg",
}
```

Required scope: `calls:write`.

`objectiveId` is optional. When provided, Oriacall associates the call with that organization objective. When omitted, Oriacall associates the call with an existing objective in the organization.

Call summaries include `callResult`, `callResultLabel`, and `callQualityScore`. `analysisStatus` is one of `pending`, `queued`, `processing`, `completed`, or `failed`. `queueStatus` is `queued`, `processing`, `completed`, `failed`, or `null` when analysis has not been queued. `analysisStage` is `audio_pass`, `text_pass`, `publishing`, `completed`, or `null` when analysis has not been queued. Internal dead-letter and cancelled runs are exposed as `failed`. Completed call analysis includes `summary`, `callStrengths`, `callWeaknesses`, `callObservations`, `objections`, and `alerts`.

## Pagination

List endpoints use cursor pagination.

```python
first_page = oriacall.calls.list({"limit": 50})

if cursor := first_page.data["pagination"]["nextCursor"]:
    second_page = oriacall.calls.list({"limit": 50, "cursor": cursor})

for call in oriacall.calls.paginate({"limit": 50}):
    print(call["id"])
```

Pagination helpers are available for `objectives`, `agents`, `calls`, `leads`, and `webhooks.endpoints`.

## Custom Field Filters

```python
oriacall.objectives.list(
    {
        "objectiveCustomFields": {
            "region": "north",
            "priority": {"gte": 5},
        }
    }
)

oriacall.calls.list(
    {
        "recordedAfter": "2026-01-01T00:00:00Z",
        "recordedBefore": "2026-02-01T00:00:00Z",
        "sortBy": "recordedAt",
        "leadCustomFields": {
            "crm_stage": "qualified",
        }
    }
)

oriacall.leads.list(
    {
        "customFields": {
            "crm_stage": "qualified",
        }
    }
)
```

For calls, `createdAfter` and `createdBefore` filter by Oriacall upload/record creation time. Use `recordedAfter`, `recordedBefore`, and `sortBy: "recordedAt"` for original call chronology. The recorded-time filters and sort fall back to `createdAt` when `recordedAt` is null.

Snake-case aliases are also accepted for SDK option names such as `lead_custom_fields`, `custom_fields`, and `objective_custom_fields`.

## Errors

Failed API calls raise `OriacallApiError`:

```python
from oriacall import OriacallApiError

try:
    oriacall.calls.get("call-id")
except OriacallApiError as error:
    print(error.status)
    print(error.code)
    print(error.message)
    print(error.request_id)
    print(error.details)
    print(error.retry_after)
    print(error.is_rate_limited)
```

## Webhook Signature Verification

```python
from oriacall import verify_webhook_signature

valid = verify_webhook_signature(
    body=request_body,
    secret=webhook_secret,
    signature=headers["Oriacall-Signature"],
    timestamp=headers["Oriacall-Timestamp"],
)
```

## Scopes

Available scopes:

```text
hello:read
objectives:read
objectives:write
objective_custom_fields:manage
agents:read
calls:read
calls:write
leads:read
leads:write
lead_custom_fields:manage
webhooks:read
webhooks:write
```

The token request can only request scopes that were granted to that API client.
