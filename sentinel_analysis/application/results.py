"""Stable, serializable output contracts returned by application use cases."""

from typing import Literal, TypedDict


IngestionStatus = Literal[
    "SUCCESS",
    "FAILED",
    "COOLDOWN_SKIPPED",
    "DISABLED_SKIPPED",
    "CANCELLED",
]


class IngestionLog(TypedDict):
    plugin: str
    status: IngestionStatus
    records: int
    error: str | None


class IngestionResult(TypedDict):
    total_inserted: int
    logs: list[IngestionLog]


def summarize_ingestion_outcome(result: IngestionResult) -> str:
    """Summarize provider-level outcomes without conflating them with HTTP success."""
    statuses = [str(log.get("status") or "").upper() for log in result.get("logs", [])]
    attempted = [status for status in statuses if status not in ("DISABLED_SKIPPED", "COOLDOWN_SKIPPED")]
    successes = sum(status == "SUCCESS" for status in attempted)
    failures = sum(status == "FAILED" for status in attempted)
    if not attempted:
        return "SKIPPED"
    if successes and failures:
        return "PARTIAL"
    if failures and not successes:
        return "FAILED"
    if successes:
        return "SUCCESS"
    return "CANCELLED" if any(status == "CANCELLED" for status in attempted) else "SKIPPED"
