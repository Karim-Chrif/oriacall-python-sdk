from __future__ import annotations

from typing import Any


class OriacallApiError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        response: dict[str, Any] | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.response = response
        self.request_id = request_id
        self.retry_after = retry_after
        self.details = details

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429
