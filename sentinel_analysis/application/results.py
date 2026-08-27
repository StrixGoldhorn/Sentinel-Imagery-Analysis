"""Stable, serializable output contracts returned by application use cases."""

from typing import Literal, TypedDict


IngestionStatus = Literal["SUCCESS", "FAILED"]


class IngestionLog(TypedDict):
    plugin: str
    status: IngestionStatus
    records: int
    error: str | None


class IngestionResult(TypedDict):
    total_inserted: int
    logs: list[IngestionLog]
