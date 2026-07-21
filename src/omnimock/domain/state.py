from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Mapping

from omnimock.domain.errors import ErrorContext, StateConflictError
from omnimock.domain.values import EventEnvelope, JsonValue


@dataclass(frozen=True, slots=True)
class JournalEntry:
    sequence: int
    request_id: str
    run_id: str
    operation_id: str
    input_digest: str
    rule_id: str
    state_version: int
    event_ids: tuple[str, ...]
    fault: str | None


@dataclass(frozen=True, slots=True)
class CommitResult:
    state_version: int
    events: tuple[EventEnvelope, ...]
    journal: JournalEntry


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class InMemoryStateStore:
    """Single-writer, deterministic state store used by tests and local runs."""

    def __init__(self, initial_state: Mapping[str, JsonValue]) -> None:
        self._state: dict[str, JsonValue] = copy.deepcopy(dict(initial_state))
        self._version = 0
        self._sequence = 0
        self._journal: list[JournalEntry] = []
        self._events: list[EventEnvelope] = []
        self._idempotency: dict[str, tuple[str, object]] = {}
        self._snapshots: dict[str, tuple[dict[str, JsonValue], int, int, list[JournalEntry], list[EventEnvelope]]] = {}
        self._lock = threading.RLock()

    def read_state(self) -> dict[str, JsonValue]:
        with self._lock:
            return copy.deepcopy(self._state)

    def state_version(self) -> int:
        with self._lock:
            return self._version

    def events(self) -> list[EventEnvelope]:
        with self._lock:
            return list(self._events)

    def journal(self) -> list[JournalEntry]:
        with self._lock:
            return list(self._journal)

    def restore_runtime(self, *, state: Mapping[str, JsonValue], version: int,
                        journal: list[JournalEntry], events: list[EventEnvelope]) -> None:
        """Restore trusted local runtime data without accepting a transition."""
        with self._lock:
            self._state = copy.deepcopy(dict(state))
            self._version = max(0, int(version))
            self._journal = list(journal)
            self._sequence = max((entry.sequence for entry in journal), default=0)
            self._events = list(events)

    def get_idempotent(self, key: str, input_digest: str) -> object | None:
        with self._lock:
            found = self._idempotency.get(key)
            if found is not None and found[0] == input_digest:
                return copy.deepcopy(found[1])
            return None

    def put_value(self, collection: str, key: str, value: JsonValue) -> int:
        with self._lock:
            collections = self._state.setdefault("collections", {})
            if not isinstance(collections, dict):
                collections = {}
                self._state["collections"] = collections
            bucket = collections.setdefault(collection, {})
            if not isinstance(bucket, dict):
                bucket = {}
                collections[collection] = bucket
            bucket[key] = copy.deepcopy(value)
            self._version += 1
            return self._version

    def get_value(self, collection: str, key: str) -> JsonValue | None:
        with self._lock:
            collections = self._state.get("collections", {})
            if not isinstance(collections, dict):
                return None
            bucket = collections.get(collection, {})
            if not isinstance(bucket, dict):
                return None
            return copy.deepcopy(bucket.get(key))

    def query(self, collection: str) -> list[JsonValue]:
        with self._lock:
            collections = self._state.get("collections", {})
            bucket = collections.get(collection, {}) if isinstance(collections, dict) else {}
            return [copy.deepcopy(value) for value in bucket.values()] if isinstance(bucket, dict) else []

    def commit(
        self,
        *,
        expected_version: int,
        state: Mapping[str, JsonValue],
        events: tuple[EventEnvelope, ...],
        journal: JournalEntry,
        idempotency_key: str | None = None,
        idempotency_value: object | None = None,
    ) -> CommitResult:
        with self._lock:
            if expected_version != self._version:
                raise StateConflictError(ErrorContext("OMC-STATE-001", "State changed before the transition committed"))
            self._state = copy.deepcopy(dict(state))
            self._version += 1
            committed = JournalEntry(
                sequence=self._sequence + 1,
                request_id=journal.request_id,
                run_id=journal.run_id,
                operation_id=journal.operation_id,
                input_digest=journal.input_digest,
                rule_id=journal.rule_id,
                state_version=self._version,
                event_ids=journal.event_ids,
                fault=journal.fault,
            )
            self._sequence += 1
            self._journal.append(committed)
            self._events.extend(events)
            if idempotency_key and idempotency_value is not None:
                self._idempotency[idempotency_key] = (journal.input_digest, copy.deepcopy(idempotency_value))
            return CommitResult(self._version, events, committed)

    def create_snapshot(self, name: str) -> None:
        with self._lock:
            self._snapshots[name] = (
                copy.deepcopy(self._state), self._version, self._sequence,
                list(self._journal), list(self._events),
            )

    def snapshot_names(self) -> list[str]:
        with self._lock:
            return sorted(self._snapshots)

    def restore_snapshot(self, name: str) -> None:
        with self._lock:
            try:
                state, version, sequence, journal, events = self._snapshots[name]
            except KeyError as exc:
                raise StateConflictError(ErrorContext("OMC-STATE-002", f"Snapshot not found: {name}")) from exc
            self._state = copy.deepcopy(state)
            self._version = version
            self._sequence = sequence
            self._journal = list(journal)
            self._events = list(events)
