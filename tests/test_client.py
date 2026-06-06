from __future__ import annotations

import json
import time
from typing import Any

import pytest
import requests

from oriacall import Oriacall, OriacallApiError, verify_webhook_signature


class FakeResponse:
    def __init__(
        self, status_code: int, body: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else json.dumps(body)

    def json(self) -> Any:
        return self._body


class FakeSession(requests.Session):
    def __init__(self, responses: list[FakeResponse]) -> None:
        super().__init__()
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:  # type: ignore[override]
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def token_response() -> FakeResponse:
    return FakeResponse(
        200,
        {"access_token": "token-123", "expires_in": 3600, "token_type": "Bearer"},
        {"X-Request-Id": "req-token"},
    )


def test_get_hello_requests_token_and_wraps_response() -> None:
    session = FakeSession(
        [
            token_response(),
            FakeResponse(200, {"message": "Hello"}, {"X-Request-Id": "req-hello"}),
        ]
    )
    client = Oriacall(client_id="client", client_secret="secret", session=session)

    response = client.hello.get()

    assert response.data == {"message": "Hello"}
    assert response.status == 200
    assert response.request_id == "req-hello"
    assert session.requests[0]["url"] == "https://api.oriacall.com/oauth/token"
    assert session.requests[1]["headers"]["Authorization"] == "Bearer token-123"


def test_list_flattens_custom_field_filters() -> None:
    session = FakeSession(
        [
            token_response(),
            FakeResponse(200, {"data": [], "pagination": {"nextCursor": None}}),
        ]
    )
    client = Oriacall(client_id="client", client_secret="secret", session=session)

    client.calls.list(
        {
            "leadCustomFields": {"crm_stage": "qualified", "score": {"gte": 5}},
            "limit": 10,
        }
    )

    assert ("leadCustom[crm_stage]", "qualified") in session.requests[1]["params"]
    assert ("leadCustom[score][gte]", "5") in session.requests[1]["params"]
    assert ("limit", "10") in session.requests[1]["params"]


def test_paginate_yields_items_until_next_cursor_is_empty() -> None:
    session = FakeSession(
        [
            token_response(),
            FakeResponse(200, {"data": [{"id": "one"}], "pagination": {"nextCursor": "cursor-2"}}),
            FakeResponse(200, {"data": [{"id": "two"}], "pagination": {"nextCursor": None}}),
        ]
    )
    client = Oriacall(client_id="client", client_secret="secret", session=session)

    assert list(client.calls.paginate({"limit": 1})) == [{"id": "one"}, {"id": "two"}]
    assert ("cursor", "cursor-2") in session.requests[2]["params"]


def test_api_errors_expose_public_error_shape() -> None:
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                422,
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Invalid input.",
                        "requestId": "req-error",
                        "details": {"limit": ["Too large."]},
                    }
                },
                {"Retry-After": "3"},
            ),
        ]
    )
    client = Oriacall(client_id="client", client_secret="secret", session=session)

    with pytest.raises(OriacallApiError) as exc_info:
        client.calls.list()

    error = exc_info.value
    assert error.status == 422
    assert error.code == "invalid_request"
    assert error.request_id == "req-error"
    assert error.retry_after == 3
    assert error.details == {"limit": ["Too large."]}
    assert not error.is_rate_limited


def test_calls_upload_sends_metadata_audio_and_idempotency_key() -> None:
    session = FakeSession(
        [
            token_response(),
            FakeResponse(201, {"data": {"id": "call-id"}}, {"X-Request-Id": "req-upload"}),
        ]
    )
    client = Oriacall(client_id="client", client_secret="secret", session=session)

    response = client.calls.upload(
        {
            "idempotencyKey": "idem-1",
            "externalId": "call-1",
            "audio": {
                "contents": b"audio-bytes",
                "filename": "call.mp3",
                "contentType": "audio/mpeg",
            },
        }
    )

    assert response.data == {"data": {"id": "call-id"}}
    request = session.requests[1]
    assert request["headers"]["Idempotency-Key"] == "idem-1"
    assert json.loads(request["data"]["metadata"]) == {"externalId": "call-1"}
    assert request["files"]["audioFile"] == ("call.mp3", b"audio-bytes", "audio/mpeg")


def test_verify_webhook_signature() -> None:
    timestamp = str(int(time.time()))
    body = '{"event":"analysis.completed"}'
    payload = f"{timestamp}.{body}".encode()
    signature = "v1=" + __import__("hmac").new(
        b"secret", payload, __import__("hashlib").sha256
    ).hexdigest()

    assert verify_webhook_signature(
        body=body, secret="secret", signature=signature, timestamp=timestamp
    )
    assert not verify_webhook_signature(
        body=body, secret="wrong", signature=signature, timestamp=timestamp
    )
