from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from omnimock.domain.values import JsonValue


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelResult:
    content: JsonValue
    profile_id: str
    materialized: bool = True


class ModelGateway(Protocol):
    def generate_structured(self, *, task: str, schema: dict[str, JsonValue], messages: Sequence[ModelMessage], seed: int) -> ModelResult: ...

