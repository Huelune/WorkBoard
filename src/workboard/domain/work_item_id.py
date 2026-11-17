from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class WorkItemId:
    """Stable identity for one work item, regardless of title or context."""

    value: str

    def __post_init__(self) -> None:
        try:
            parsed = UUID(self.value)
        except ValueError as exc:
            raise ValueError("work item ID must be a UUIDv4 string") from exc
        if parsed.version != 4 or str(parsed) != self.value:
            raise ValueError("work item ID must be a canonical UUIDv4 string")

    @classmethod
    def new(cls) -> WorkItemId:
        return cls(str(uuid4()))
