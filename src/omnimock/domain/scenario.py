from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from omnimock.domain.errors import ConfigurationError, ErrorContext
from omnimock.domain.values import JsonValue


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    id: str
    kind: str
    plugin: str
    mode: str
    state_namespace: str
    listen: Mapping[str, JsonValue] = field(default_factory=dict)
    root: str | None = None
    contract: str | None = None
    behavior: str | None = None


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    id: str
    priority: int
    when: Mapping[str, JsonValue]
    validate: tuple[Mapping[str, JsonValue], ...] = ()
    mutate: tuple[Mapping[str, JsonValue], ...] = ()
    emit: tuple[Mapping[str, JsonValue], ...] = ()
    respond: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FaultPolicy:
    timeout_probability: float = 0.0
    error_probability: float = 0.0
    error_status: int = 503
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    id: str
    version: str
    seed: int
    initial_state: Mapping[str, JsonValue]
    rules: tuple[RuleDefinition, ...]
    faults: FaultPolicy = FaultPolicy()
    start: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    revision_digest: str | None = None

    @property
    def revision(self) -> str:
        # The loader replaces this with a digest of the source; this fallback is
        # stable and useful for programmatically-created scenarios.
        return self.revision_digest or f"{self.id}@{self.version}"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(ErrorContext("OMC-SCENARIO-001", f"{label} must be a mapping"))
    return value


def scenario_from_mapping(raw: Mapping[str, Any]) -> ScenarioDefinition:
    data = _mapping(raw, "scenario")
    required = ("schema_version", "id", "version", "seed", "initial_state", "rules")
    missing = [name for name in required if name not in data]
    if missing:
        raise ConfigurationError(ErrorContext("OMC-SCENARIO-002", f"Missing scenario fields: {', '.join(missing)}"))
    if str(data["schema_version"]) != "1":
        raise ConfigurationError(ErrorContext("OMC-SCENARIO-003", "Unsupported scenario schema version"))
    rules: list[RuleDefinition] = []
    seen_priorities: dict[int, str] = {}
    raw_rules = data["rules"]
    if not isinstance(raw_rules, list):
        raise ConfigurationError(ErrorContext("OMC-SCENARIO-004", "scenario.rules must be a list"))
    for raw_rule in raw_rules:
        rule = _mapping(raw_rule, "scenario rule")
        rule_id = str(rule.get("id", ""))
        if not rule_id or "priority" not in rule or "when" not in rule:
            raise ConfigurationError(ErrorContext("OMC-SCENARIO-005", "Every rule needs id, priority, and when"))
        priority = int(rule["priority"])
        if priority in seen_priorities:
            raise ConfigurationError(ErrorContext("OMC-SCENARIO-006", f"Rule priority tie: {rule_id} and {seen_priorities[priority]}"))
        seen_priorities[priority] = rule_id
        rules.append(
            RuleDefinition(
                id=rule_id,
                priority=priority,
                when=_mapping(rule["when"], f"rule {rule_id}.when"),
                validate=tuple(_mapping(item, f"rule {rule_id}.validate") for item in rule.get("validate", [])),
                mutate=tuple(_mapping(item, f"rule {rule_id}.mutate") for item in rule.get("mutate", [])),
                emit=tuple(_mapping(item, f"rule {rule_id}.emit") for item in rule.get("emit", [])),
                respond=_mapping(rule.get("respond", {}), f"rule {rule_id}.respond"),
            )
        )
    raw_faults = _mapping(data.get("faults", {}), "scenario.faults")
    profiles = raw_faults.get("profiles", {})
    profile = {}
    if isinstance(profiles, Mapping) and profiles:
        first_name = sorted(profiles)[0]
        selected = profiles[first_name]
        if isinstance(selected, Mapping):
            profile = selected
    return ScenarioDefinition(
        id=str(data["id"]),
        version=str(data["version"]),
        seed=int(data["seed"]),
        initial_state=_mapping(data["initial_state"], "scenario.initial_state"),
        rules=tuple(sorted(rules, key=lambda item: item.priority, reverse=True)),
        faults=FaultPolicy(
            timeout_probability=float(profile.get("timeout_probability", 0.0)),
            error_probability=float(profile.get("error_probability", 0.0)),
            error_status=int(profile.get("error_status", 503)),
            latency_ms=int(profile.get("latency_ms", 0)),
        ),
    )
