from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    claims: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    scenario_revision: str
    seed: int
    state_version: int
    rule_id: str | None = None
    contract_digest: str | None = None
    model_profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class RequestEnvelope:
    request_id: str
    run_id: str
    service_id: str
    operation_id: str
    protocol: str
    received_at: datetime
    logical_time: datetime
    metadata: dict[str, JsonValue]
    payload: JsonValue | bytes | None
    principal: Principal | None = None


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    source: str
    subject: str | None
    occurred_at: datetime
    logical_time: datetime
    schema_ref: str | None
    headers: dict[str, JsonValue]
    payload: JsonValue | bytes | None


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    outcome: str
    metadata: dict[str, JsonValue]
    payload: JsonValue | bytes | None
    state_version: int
    provenance: Provenance

