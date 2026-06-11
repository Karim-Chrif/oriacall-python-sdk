from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from .errors import OriacallApiError
from .response import ApiResponse

ResponseHook = Callable[[dict[str, Any]], None]


class Oriacall:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.oriacall.com",
        scope: str | list[str] | tuple[str, ...] | None = None,
        session: requests.Session | None = None,
        on_response: ResponseHook | None = None,
        retries: int = 0,
        retry_base_delay_ms: int = 250,
        retry_max_delay_ms: int = 2000,
        timeout_seconds: int = 30,
    ) -> None:
        if not client_id or not client_secret:
            raise OriacallApiError(
                0, "invalid_sdk_input", "client_id and client_secret are required."
            )

        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.scope = scope
        self.session = session or requests.Session()
        self.on_response = on_response
        self.retries = retries
        self.retry_base_delay_ms = retry_base_delay_ms
        self.retry_max_delay_ms = retry_max_delay_ms
        self.timeout_seconds = timeout_seconds
        self._access_token: str | None = None
        self._token_expires_at: float | None = None

        self.hello = HelloResource(self)
        self.objectives = ObjectivesResource(self)
        self.agents = AgentsResource(self)
        self.calls = CallsResource(self)
        self.leads = LeadsResource(self)
        self.lead_custom_fields = CustomFieldsResource(self, "/v1/lead-custom-fields")
        self.objective_custom_fields = CustomFieldsResource(self, "/v1/objective-custom-fields")
        self.webhooks = WebhooksResource(self)

    def get_access_token(self) -> str:
        if (
            self._access_token
            and self._token_expires_at
            and time.time() < self._token_expires_at - 30
        ):
            return self._access_token

        response = self._request(
            "POST",
            "/oauth/token",
            headers={"Content-Type": "application/json"},
            json_body={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self._format_scope(self.scope),
            },
            retryable=True,
        )

        token = response.data.get("access_token") if isinstance(response.data, dict) else None
        expires_in = response.data.get("expires_in") if isinstance(response.data, dict) else None
        if not token:
            raise OriacallApiError(
                response.status,
                "invalid_response",
                "Oriacall returned an invalid token response.",
                request_id=response.request_id,
            )

        self._access_token = str(token)
        self._token_expires_at = time.time() + int(expires_in or 0)
        return self._access_token

    def raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        data: str | bytes | None = None,
        auth: bool = True,
    ) -> ApiResponse:
        request_headers = dict(headers or {})
        if auth:
            request_headers.update(self._auth_headers())

        return self._request(
            method.upper(),
            path,
            params=params,
            headers=request_headers,
            json_body=json_body,
            data=data,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> ApiResponse:
        return self._request(
            "GET",
            path,
            params=params,
            headers=self._auth_headers(),
            retryable=True,
        )

    def json(self, method: str, path: str, body: dict[str, Any]) -> ApiResponse:
        return self._request(
            method,
            path,
            headers=self._auth_headers({"Content-Type": "application/json"}),
            json_body=body,
        )

    def delete(self, path: str) -> ApiResponse:
        return self._request("DELETE", path, headers=self._auth_headers(), expect_json=False)

    def multipart(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str],
        files: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        return self._request(
            method,
            path,
            headers=self._auth_headers(headers or {}),
            data=data,
            files=files,
        )

    def encode_path(self, value: str) -> str:
        return quote(value, safe="")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, str] | str | bytes | None = None,
        files: dict[str, Any] | None = None,
        expect_json: bool = True,
        retryable: bool = False,
    ) -> ApiResponse:
        attempt = 0

        while True:
            try:
                response = self.session.request(
                    method,
                    self._url(path),
                    params=self._flatten_params(params or {}),
                    headers=headers or {},
                    json=json_body,
                    data=data,
                    files=files,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                raise OriacallApiError(0, "request_failed", str(exc)) from exc

            body = self._decode_body(response)
            request_id = response.headers.get("X-Request-Id") or self._body_request_id(body)
            retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
            self._notify_response(method, path, response.status_code, request_id, retry_after)

            if 200 <= response.status_code < 300:
                if not expect_json and body is None:
                    return ApiResponse(None, response.status_code, request_id)
                if not isinstance(body, dict):
                    raise OriacallApiError(
                        response.status_code,
                        "invalid_response",
                        "Oriacall returned an invalid response.",
                        request_id=request_id,
                        retry_after=retry_after,
                    )
                return ApiResponse(body, response.status_code, request_id)

            if retryable and attempt < self.retries and self._should_retry(response.status_code):
                time.sleep(self._retry_delay_seconds(attempt, retry_after))
                attempt += 1
                continue

            error = body.get("error") if isinstance(body, dict) else None
            raise OriacallApiError(
                response.status_code,
                str(error.get("code", "api_request_failed"))
                if isinstance(error, dict)
                else "api_request_failed",
                str(error.get("message", "Oriacall API request failed."))
                if isinstance(error, dict)
                else "Oriacall API request failed.",
                body if isinstance(body, dict) else None,
                request_id,
                retry_after,
                error.get("details") if isinstance(error, dict) else None,
            )

    def _auth_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        merged = dict(headers or {})
        merged["Authorization"] = f"Bearer {self.get_access_token()}"
        return merged

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _format_scope(self, scope: str | list[str] | tuple[str, ...] | None) -> str | None:
        if scope is None:
            return None
        if isinstance(scope, str):
            return scope
        return " ".join(scope)

    def _flatten_params(self, params: dict[str, Any]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        aliases = {
            "objective_custom_fields": "objectiveCustom",
            "objectiveCustomFields": "objectiveCustom",
            "lead_custom_fields": "leadCustom",
            "leadCustomFields": "leadCustom",
            "custom_fields": "custom",
            "customFields": "custom",
        }

        for key, value in params.items():
            if value is None:
                continue
            api_key = aliases.get(key, key)
            if api_key in {"objectiveCustom", "leadCustom", "custom"} and isinstance(value, dict):
                for field, field_value in value.items():
                    if isinstance(field_value, dict):
                        for operator, operator_value in field_value.items():
                            pairs.append((f"{api_key}[{field}][{operator}]", str(operator_value)))
                    else:
                        pairs.append((f"{api_key}[{field}]", str(field_value)))
            else:
                pairs.append((api_key, str(value)))
        return pairs

    def _decode_body(self, response: requests.Response) -> Any:
        if response.text == "":
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _body_request_id(self, body: Any) -> str | None:
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            request_id = body["error"].get("requestId")
            return str(request_id) if request_id else None
        return None

    def _retry_after_seconds(self, value: str | None) -> int | None:
        if not value:
            return None
        try:
            seconds = int(value)
        except ValueError:
            return None
        return max(seconds, 0)

    def _should_retry(self, status: int) -> bool:
        return status in {429, 500, 502, 503, 504}

    def _retry_delay_seconds(self, attempt: int, retry_after: int | None) -> float:
        if retry_after is not None:
            return retry_after
        delay_ms = min(self.retry_base_delay_ms * (2**attempt), self.retry_max_delay_ms)
        return delay_ms / 1000

    def _notify_response(
        self,
        method: str,
        path: str,
        status: int,
        request_id: str | None,
        retry_after: int | None,
    ) -> None:
        if not self.on_response:
            return
        self.on_response(
            {
                "method": method,
                "path": path,
                "status": status,
                "requestId": request_id,
                "request_id": request_id,
                "retryAfter": retry_after,
                "retry_after": retry_after,
            }
        )


class Paginates:
    def paginate(self, options: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        cursor = (options or {}).get("cursor")
        while True:
            page_options = dict(options or {})
            if cursor:
                page_options["cursor"] = cursor
            else:
                page_options.pop("cursor", None)

            response = self.list(page_options)  # type: ignore[attr-defined]
            yield from response.data.get("data", [])

            cursor = response.data.get("pagination", {}).get("nextCursor")
            if not cursor:
                break


class HelloResource:
    def __init__(self, client: Oriacall) -> None:
        self.client = client

    def get(self) -> ApiResponse:
        return self.client.get("/v1/hello")


class ObjectivesResource(Paginates):
    def __init__(self, client: Oriacall) -> None:
        self.client = client

    def list(self, options: dict[str, Any] | None = None) -> ApiResponse:
        return self.client.get("/v1/objectives", options or {})

    def update(self, objective_id: str, body: dict[str, Any]) -> ApiResponse:
        return self.client.json(
            "PATCH", f"/v1/objectives/{self.client.encode_path(objective_id)}", body
        )


class AgentsResource(Paginates):
    def __init__(self, client: Oriacall) -> None:
        self.client = client

    def list(self, options: dict[str, Any] | None = None) -> ApiResponse:
        return self.client.get("/v1/agents", options or {})


class CallsResource(Paginates):
    def __init__(self, client: Oriacall) -> None:
        self.client = client

    def list(self, options: dict[str, Any] | None = None) -> ApiResponse:
        return self.client.get("/v1/calls", options or {})

    def get(self, call_id: str) -> ApiResponse:
        return self.client.get(f"/v1/calls/{self.client.encode_path(call_id)}")

    def upload(self, body: dict[str, Any]) -> ApiResponse:
        idempotency_key = body.get("idempotencyKey") or body.get("idempotency_key")
        if not idempotency_key:
            raise OriacallApiError(0, "invalid_sdk_input", "calls.upload requires idempotencyKey.")

        audio = body.get("audio")
        if not isinstance(audio, dict):
            raise OriacallApiError(0, "invalid_sdk_input", "calls.upload requires an audio dict.")

        metadata = dict(body)
        metadata.pop("idempotencyKey", None)
        metadata.pop("idempotency_key", None)
        metadata.pop("audio", None)

        filename = audio.get("filename") or "call-audio"
        content_type = (
            audio.get("contentType") or audio.get("content_type") or "application/octet-stream"
        )
        close_after = None
        contents = audio.get("contents")
        if contents is None and audio.get("path"):
            close_after = Path(audio["path"]).open("rb")
            contents = close_after
        if contents is None:
            raise OriacallApiError(
                0,
                "invalid_sdk_input",
                "calls.upload audio requires contents or path.",
            )

        try:
            return self.client.multipart(
                "POST",
                "/v1/calls",
                data={"metadata": json.dumps(metadata)},
                files={"audioFile": (filename, contents, content_type)},
                headers={"Idempotency-Key": str(idempotency_key)},
            )
        finally:
            if close_after is not None:
                close_after.close()

    def queue_analysis(self, call_id: str) -> ApiResponse:
        return self.client.json(
            "POST", f"/v1/calls/{self.client.encode_path(call_id)}/analysis-jobs", {}
        )

    def wait_for_analysis(
        self, call_id: str, options: dict[str, Any] | None = None
    ) -> ApiResponse:
        interval_ms = int(
            (options or {}).get("intervalMs") or (options or {}).get("interval_ms") or 2000
        )
        timeout_ms = int(
            (options or {}).get("timeoutMs") or (options or {}).get("timeout_ms") or 120000
        )
        started_at = time.monotonic()

        while True:
            response = self.get(call_id)
            status = response.data.get("data", {}).get("analysisStatus")
            if status in {"completed", "failed"}:
                return response
            if (time.monotonic() - started_at) * 1000 >= timeout_ms:
                raise OriacallApiError(
                    408,
                    "analysis_timeout",
                    "Timed out waiting for call analysis.",
                    request_id=response.request_id,
                )
            time.sleep(interval_ms / 1000)


class LeadsResource(Paginates):
    def __init__(self, client: Oriacall) -> None:
        self.client = client

    def list(self, options: dict[str, Any] | None = None) -> ApiResponse:
        return self.client.get("/v1/leads", options or {})

    def get(self, lead_id: str) -> ApiResponse:
        return self.client.get(f"/v1/leads/{self.client.encode_path(lead_id)}")

    def update(self, lead_id: str, body: dict[str, Any]) -> ApiResponse:
        return self.client.json("PATCH", f"/v1/leads/{self.client.encode_path(lead_id)}", body)

    def upsert_by_external_id(self, external_id: str, body: dict[str, Any]) -> ApiResponse:
        return self.client.json(
            "PUT", f"/v1/leads/by-external-id/{self.client.encode_path(external_id)}", body
        )


class CustomFieldsResource:
    def __init__(self, client: Oriacall, path: str) -> None:
        self.client = client
        self.path = path

    def list(self, options: dict[str, Any] | None = None) -> ApiResponse:
        return self.client.get(self.path, options or {})

    def create(self, body: dict[str, Any]) -> ApiResponse:
        return self.client.json("POST", self.path, body)

    def update(self, key: str, body: dict[str, Any]) -> ApiResponse:
        return self.client.json("PATCH", f"{self.path}/{self.client.encode_path(key)}", body)


class WebhooksResource:
    def __init__(self, client: Oriacall) -> None:
        self.client = client
        self.endpoints = WebhookEndpointsResource(client)


class WebhookEndpointsResource(Paginates):
    def __init__(self, client: Oriacall) -> None:
        self.client = client

    def list(self, options: dict[str, Any] | None = None) -> ApiResponse:
        return self.client.get("/v1/webhooks/endpoints", options or {})

    def create(self, body: dict[str, Any]) -> ApiResponse:
        return self.client.json("POST", "/v1/webhooks/endpoints", body)

    def update(self, endpoint_id: str, body: dict[str, Any]) -> ApiResponse:
        return self.client.json(
            "PATCH", f"/v1/webhooks/endpoints/{self.client.encode_path(endpoint_id)}", body
        )

    def delete(self, endpoint_id: str) -> ApiResponse:
        return self.client.delete(f"/v1/webhooks/endpoints/{self.client.encode_path(endpoint_id)}")

    def rotate_secret(self, endpoint_id: str) -> ApiResponse:
        path = f"/v1/webhooks/endpoints/{self.client.encode_path(endpoint_id)}/rotate-secret"
        return self.client.json(
            "POST", path, {}
        )

    def test(self, endpoint_id: str) -> ApiResponse:
        return self.client.json(
            "POST", f"/v1/webhooks/endpoints/{self.client.encode_path(endpoint_id)}/test", {}
        )


def create_client(**options: Any) -> Oriacall:
    return Oriacall(**options)


def verify_webhook_signature(
    *,
    body: str | bytes,
    secret: str,
    signature: str,
    timestamp: str,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> bool:
    if not body or not secret or not signature or not timestamp:
        return False

    try:
        timestamp_value = int(timestamp)
    except ValueError:
        return False

    current_time = int(now if now is not None else time.time())
    if abs(current_time - timestamp_value) > tolerance_seconds:
        return False

    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    signed_payload = str(timestamp_value).encode("utf-8") + b"." + body_bytes
    expected = "v1=" + hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
