from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    data: Any
    status: int
    request_id: str | None = None
