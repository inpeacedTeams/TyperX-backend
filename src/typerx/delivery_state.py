"""In-memory delivery accounting, separate from model conversation history."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DeliveryState:
    kind: str
    source_message_ids: tuple[int, ...]
    parts: tuple[str, ...]
    confirmed_message_ids: list[int] = field(default_factory=list)
    in_flight_index: int | None = None
    status: str = "ready"

    @property
    def confirmed_count(self) -> int:
        return len(self.confirmed_message_ids)

    def snapshot(self) -> dict:
        count = self.confirmed_count
        rest_start = count + (1 if self.in_flight_index is not None else 0)
        return {
            "kind": self.kind,
            "source_message_ids": list(self.source_message_ids),
            "status": self.status,
            "generated_text": " ".join(self.parts),
            "confirmed_text": " ".join(self.parts[:count]),
            "confirmed_parts": list(self.parts[:count]),
            "confirmed_message_ids": list(self.confirmed_message_ids),
            "in_flight_part": (self.parts[self.in_flight_index]
                               if self.in_flight_index is not None else None),
            "remaining_parts": list(self.parts[rest_start:]),
        }
