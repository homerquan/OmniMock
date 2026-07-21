from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnimock.domain.behavior import BehaviorEngine
from omnimock.domain.scenario import ScenarioDefinition
from omnimock.domain.state import InMemoryStateStore
from omnimock.domain.values import RequestEnvelope, ResponseEnvelope
from omnimock.infrastructure.config.loader import ProjectConfig


class SimulationRuntime:
    def __init__(self, project: ProjectConfig, scenario: ScenarioDefinition) -> None:
        self.project = project
        self.scenario = scenario
        self.store = InMemoryStateStore(scenario.initial_state)
        self.engine = BehaviorEngine(scenario, self.store)
        self._request_counter = 0
        self._lock = threading.Lock()
        self._load_persisted()
        self._materialize_file_events()

    def request(self, service_id: str, operation_id: str, payload: Any = None, metadata: dict[str, Any] | None = None,
                protocol: str = "http") -> ResponseEnvelope:
        with self._lock:
            self._request_counter += 1
            sequence = self._request_counter
        request_id = f"req-{sequence:08d}"
        logical_time = self.scenario.start.replace(microsecond=0)
        response = self.engine.handle(RequestEnvelope(request_id, "local", service_id, operation_id, protocol,
                                                       logical_time, logical_time, metadata or {}, payload))
        self._materialize_file_events()
        self._persist()
        return response

    def _materialize_file_events(self) -> None:
        roots = [self.project.root / (service.root or ".omnimock/mounts/default")
                 for service in self.project.services if service.kind == "filesystem" and service.mode == "native"]
        if not roots:
            return
        for event in self.store.events():
            if event.event_type != "filesystem.write" or not isinstance(event.payload, str):
                continue
            relative = str(event.headers.get("path", ""))
            for root in roots:
                root_resolved = root.resolve()
                candidate = (root_resolved / relative).resolve()
                if root_resolved not in candidate.parents or candidate == root_resolved or len(relative) > 512:
                    continue
                candidate.parent.mkdir(parents=True, exist_ok=True)
                temporary = candidate.with_suffix(candidate.suffix + ".tmp")
                temporary.write_text(event.payload, encoding="utf-8")
                temporary.replace(candidate)
                candidate.chmod(0o600)

    def snapshot_create(self, name: str) -> None:
        _validate_snapshot_name(name)
        self.store.create_snapshot(name)
        path = self._snapshot_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._runtime_data(), sort_keys=True, default=str), encoding="utf-8")
        self._persist()

    def snapshot_restore(self, name: str) -> None:
        _validate_snapshot_name(name)
        if name in self.store.snapshot_names():
            self.store.restore_snapshot(name)
        else:
            path = self._snapshot_path(name)
            if not path.exists():
                self.store.restore_snapshot(name)
            self._restore_data(json.loads(path.read_text(encoding="utf-8")))
        self._persist()

    def snapshot_names(self) -> list[str]:
        names = set(self.store.snapshot_names())
        directory = self._snapshot_path("placeholder").parent
        if directory.exists():
            names.update(path.stem for path in directory.glob("*.json"))
        return sorted(names)

    def _snapshot_path(self, name: str) -> Path:
        return self.project.root / ".omnimock" / "snapshots" / f"{name}.json"

    def _runtime_data(self) -> dict[str, Any]:
        return {"state": self.store.read_state(), "version": self.store.state_version(),
                "journal": [asdict(entry) for entry in self.store.journal()],
                "events": [asdict(event) for event in self.store.events()]}

    def _state_path(self) -> Path:
        configured = self.project.raw.get("state", {}).get("sqlite_path", ".omnimock/state.json")
        path = self.project.root / str(configured)
        return path.with_suffix(".json") if path.suffix == ".db" else path

    def _persist(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._runtime_data()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(path)

    def _load_persisted(self) -> None:
        # Runtime persistence restores local state and evidence; it never
        # accepts persisted data as an unvalidated transition.
        path = self._state_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            state = raw.get("state")
            if isinstance(state, dict):
                self.store = InMemoryStateStore(state)
                from omnimock.domain.state import JournalEntry
                from omnimock.domain.values import EventEnvelope
                journal = [JournalEntry(
                    sequence=int(item["sequence"]), request_id=str(item["request_id"]), run_id=str(item["run_id"]),
                    operation_id=str(item["operation_id"]), input_digest=str(item["input_digest"]),
                    rule_id=str(item["rule_id"]), state_version=int(item["state_version"]),
                    event_ids=tuple(item.get("event_ids", [])), fault=item.get("fault"),
                ) for item in raw.get("journal", []) if isinstance(item, dict)]
                events = []
                for item in raw.get("events", []):
                    if not isinstance(item, dict):
                        continue
                    occurred_at = datetime.fromisoformat(str(item["occurred_at"]).replace("Z", "+00:00"))
                    logical_time = datetime.fromisoformat(str(item["logical_time"]).replace("Z", "+00:00"))
                    events.append(EventEnvelope(str(item["event_id"]), str(item["event_type"]), str(item["source"]),
                                                item.get("subject"), occurred_at, logical_time, item.get("schema_ref"),
                                                item.get("headers", {}), item.get("payload")))
                self.store.restore_runtime(state=state, version=int(raw.get("version", 0)), journal=journal, events=events)
                self._request_counter = max((entry.sequence for entry in journal), default=0)
                self.engine = BehaviorEngine(self.scenario, self.store)
        except (OSError, json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError):
            # A corrupt runtime cache must not prevent deterministic startup;
            # validate/repair commands can remove it explicitly.
            return

    def _restore_data(self, raw: dict[str, Any]) -> None:
        from omnimock.domain.state import JournalEntry
        from omnimock.domain.values import EventEnvelope
        state = raw.get("state", {})
        journal = [JournalEntry(
            sequence=int(item["sequence"]), request_id=str(item["request_id"]), run_id=str(item["run_id"]),
            operation_id=str(item["operation_id"]), input_digest=str(item["input_digest"]),
            rule_id=str(item["rule_id"]), state_version=int(item["state_version"]),
            event_ids=tuple(item.get("event_ids", [])), fault=item.get("fault"),
        ) for item in raw.get("journal", []) if isinstance(item, dict)]
        events = [_event_from_data(item) for item in raw.get("events", []) if isinstance(item, dict)]
        self.store.restore_runtime(state=state, version=int(raw.get("version", 0)), journal=journal, events=events)
        self._request_counter = max((entry.sequence for entry in journal), default=0)
        self.engine = BehaviorEngine(self.scenario, self.store)


def _event_from_data(item: dict[str, Any]) -> Any:
    from omnimock.domain.values import EventEnvelope
    occurred_at = datetime.fromisoformat(str(item["occurred_at"]).replace("Z", "+00:00"))
    logical_time = datetime.fromisoformat(str(item["logical_time"]).replace("Z", "+00:00"))
    return EventEnvelope(str(item["event_id"]), str(item["event_type"]), str(item["source"]), item.get("subject"),
                         occurred_at, logical_time, item.get("schema_ref"), item.get("headers", {}), item.get("payload"))


def _validate_snapshot_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
        raise ValueError("snapshot name must be a bounded identifier")
