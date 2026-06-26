from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session


@dataclass
class LoopResult:
    loop_name: str
    consumed_events: int = 0
    emitted_events: int = 0
    created_records: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def inc(self, key: str, amount: int = 1) -> None:
        self.created_records[key] = self.created_records.get(key, 0) + amount


class BaseLoop:
    name = "base"
    consumes: tuple[str, ...] = ()

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        raise NotImplementedError
