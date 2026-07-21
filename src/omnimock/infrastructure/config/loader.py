from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from omnimock.domain.errors import ConfigurationError, ErrorContext
from omnimock.domain.scenario import ServiceDefinition, ScenarioDefinition, scenario_from_mapping
from omnimock.infrastructure.config.yaml_loader import load_document


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    root: Path
    raw: Mapping[str, Any]
    services: tuple[ServiceDefinition, ...]
    scenario_path: Path

    @property
    def runtime_host(self) -> str:
        runtime = self.raw["runtime"]
        return str(runtime.get("host", "127.0.0.1"))

    @property
    def control_port(self) -> int:
        runtime = self.raw["runtime"]
        return int(runtime.get("control_port", 7777))

    @property
    def seed(self) -> int:
        return int(self.raw["runtime"].get("seed", 0))


def find_root(path: Path | None = None) -> Path:
    candidate = (path or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / "omnimock.yaml").exists():
            return directory
    raise ConfigurationError(ErrorContext("OMC-CONFIG-004", "No omnimock.yaml found", source=str(candidate)))


def load_project(path: Path | None = None) -> ProjectConfig:
    root = find_root(path)
    config_path = root / "omnimock.yaml"
    raw = load_document(config_path)
    if not isinstance(raw, Mapping):
        raise ConfigurationError(ErrorContext("OMC-CONFIG-005", "Root configuration must be a mapping", source=str(config_path)))
    _validate_root(raw, config_path)
    services: list[ServiceDefinition] = []
    for service in raw["services"]:
        if not isinstance(service, Mapping):
            raise ConfigurationError(ErrorContext("OMC-CONFIG-006", "Each service must be a mapping", source=str(config_path)))
        services.append(ServiceDefinition(
            id=str(service["id"]), kind=str(service["kind"]), plugin=str(service["plugin"]), mode=str(service["mode"]),
            state_namespace=str(service.get("state_namespace", "default")),
            listen=service.get("listen", {}), root=str(service["root"]) if "root" in service else None,
            contract=str(service["contract"]) if "contract" in service else None,
        ))
    scenario_name = str(raw["project"].get("default_scenario", "checkout"))
    scenario_path = root / str(raw["config"].get("scenario_dir", "scenarios")) / f"{scenario_name}.yaml"
    return ProjectConfig(root, raw, tuple(services), scenario_path)


def load_scenario(project: ProjectConfig, scenario: str | None = None) -> ScenarioDefinition:
    scenario_name = scenario or str(project.raw["project"].get("default_scenario", "checkout"))
    scenario_dir = project.root / str(project.raw["config"].get("scenario_dir", "scenarios"))
    path = scenario_dir / f"{scenario_name}.yaml"
    raw = load_document(path)
    if not isinstance(raw, Mapping):
        raise ConfigurationError(ErrorContext("OMC-SCENARIO-007", "Scenario must be a mapping", source=str(path)))
    base = scenario_from_mapping(raw)
    source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    start = base.start
    runtime_clock = project.raw.get("runtime", {}).get("clock", {})
    if isinstance(runtime_clock, Mapping) and isinstance(runtime_clock.get("start"), str):
        try:
            start = datetime.fromisoformat(str(runtime_clock["start"]).replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ConfigurationError(ErrorContext("OMC-CONFIG-018", "runtime.clock.start must be an ISO-8601 timestamp", source=str(project.root / "omnimock.yaml"))) from exc
    return ScenarioDefinition(base.id, base.version, base.seed, base.initial_state, base.rules, base.faults, start,
                               "sha256:" + source_digest)


def _validate_root(raw: Mapping[str, Any], path: Path) -> None:
    _reject_unknown(raw, {"$schema", "schema_version", "project", "runtime", "config", "state", "models", "observability", "services"}, "root", path)
    required = ("schema_version", "project", "runtime", "config", "state", "models", "services")
    missing = [field for field in required if field not in raw]
    if missing:
        raise ConfigurationError(ErrorContext("OMC-CONFIG-007", f"Missing required fields: {', '.join(missing)}", source=str(path)))
    if str(raw["schema_version"]) != "1":
        raise ConfigurationError(ErrorContext("OMC-CONFIG-008", "Unsupported configuration schema version", source=str(path)))
    for field in ("project", "runtime", "config", "state", "models"):
        if not isinstance(raw[field], Mapping):
            raise ConfigurationError(ErrorContext("OMC-CONFIG-009", f"{field} must be a mapping", source=str(path)))
    _reject_unknown(raw["project"], {"id", "default_scenario", "artifact_dir"}, "project", path)
    _reject_unknown(raw["runtime"], {"host", "control_port", "strict", "seed", "clock", "generation_mode", "outbound_network"}, "runtime", path)
    _reject_unknown(raw["config"], {"model_dir", "scenario_dir", "contract_dir", "fixture_dir", "recording_dir"}, "config", path)
    _reject_unknown(raw["state"], {"driver", "url_env", "sqlite_path", "snapshot_interval"}, "state", path)
    _reject_unknown(raw["models"], {"routing_file"}, "models", path)
    services = raw["services"]
    if not isinstance(services, list):
        raise ConfigurationError(ErrorContext("OMC-CONFIG-010", "services must be a list", source=str(path)))
    ids: set[str] = set()
    for service in services:
        if not isinstance(service, Mapping):
            raise ConfigurationError(ErrorContext("OMC-CONFIG-011", "service must be a mapping", source=str(path)))
        _reject_unknown(service, {"id", "kind", "plugin", "mode", "contract", "listen", "managed", "root", "behavior", "state_namespace", "limits", "faults", "auth", "tags"}, "service", path)
        missing_service = [field for field in ("id", "kind", "plugin", "mode") if field not in service]
        if missing_service:
            raise ConfigurationError(ErrorContext("OMC-CONFIG-012", f"Service missing: {', '.join(missing_service)}", source=str(path)))
        service_id = str(service["id"])
        if service_id in ids:
            raise ConfigurationError(ErrorContext("OMC-CONFIG-013", f"Duplicate service id: {service_id}", source=str(path)))
        ids.add(service_id)
        if str(service["mode"]) not in {"native", "managed", "proxy", "replay", "hybrid"}:
            raise ConfigurationError(ErrorContext("OMC-CONFIG-014", f"Unsupported service mode: {service['mode']}", source=str(path)))
        if "listen" in service and not isinstance(service["listen"], Mapping):
            raise ConfigurationError(ErrorContext("OMC-CONFIG-016", "service.listen must be a mapping", source=str(path)))
        if isinstance(service.get("listen"), Mapping):
            _reject_unknown(service["listen"], {"host", "port", "path"}, "service.listen", path)
    runtime = raw["runtime"]
    if runtime.get("outbound_network", "deny") != "deny":
        raise ConfigurationError(ErrorContext("OMC-SECURITY-001", "Outbound network must remain deny by default", source=str(path)))
    host = str(runtime.get("host", "127.0.0.1"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError(ErrorContext("OMC-SECURITY-002", "Runtime host must be loopback", source=str(path)))
    port = int(runtime.get("control_port", 7777))
    if not 0 <= port <= 65535:
        raise ConfigurationError(ErrorContext("OMC-CONFIG-015", "control_port must be 0..65535", source=str(path)))


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str, path: Path) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ConfigurationError(ErrorContext("OMC-CONFIG-017", f"Unknown {label} field(s): {', '.join(unknown)}", source=str(path)))


def resolved_config(project: ProjectConfig) -> dict[str, Any]:
    value = json.loads(json.dumps(project.raw))
    value.setdefault("runtime", {})["resolved_root"] = str(project.root)
    return value
