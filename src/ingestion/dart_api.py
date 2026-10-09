"""OpenDART status validation without returning provider messages or credentials."""
from __future__ import annotations

import re
from typing import Any


class DartAPIError(RuntimeError):
    pass


class DartProviderMaintenance(DartAPIError):
    """The provider is unavailable; individual items must remain pending."""

    def __init__(self):
        super().__init__("DART provider maintenance status=800")


def read_dart_payload(response: Any) -> dict:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        raise DartAPIError("DART invalid JSON response") from None
    if not isinstance(payload, dict):
        raise DartAPIError("DART invalid response object")
    status = payload.get("status")
    if not isinstance(status, str) or not re.fullmatch(r"[0-9]{3}", status):
        raise DartAPIError("DART missing or invalid status")
    if status == "800":
        raise DartProviderMaintenance()
    if status not in {"000", "013"}:
        raise DartAPIError(f"DART provider error status={status}")
    if status == "013" and payload.get("list") not in (None, []):
        raise DartAPIError("DART no-data response contains records")
    return payload
