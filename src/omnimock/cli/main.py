from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from omnimock import __version__
from omnimock.application.runtime import SimulationRuntime
from omnimock.domain.behavior import BehaviorEngine
from omnimock.domain.errors import ConfigurationError, ContractError, ErrorContext, OmniMockError
from omnimock.domain.state import InMemoryStateStore
from omnimock.infrastructure.config.loader import ProjectConfig, load_project, load_scenario, resolved_config
from omnimock.infrastructure.config.yaml_loader import load_document
from omnimock.infrastructure.models import load_profiles
from omnimock.simulators.http import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnimock", description="Deterministic contract-first service simulation")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--root", type=Path, help="Project directory or path inside a project")
    parser.add_argument("--output", choices=("text", "json"), default="text")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create a starter OmniMock project")
    sub.add_parser("validate", help="Validate project configuration, models, contracts, and scenario")
    sub.add_parser("doctor", help="Run local safety and configuration diagnostics")
    for command in ("compile", "generate", "serve", "run"):
        item = sub.add_parser(command)
        item.add_argument("scenario", nargs="?", default=None)
        if command == "run":
            item.add_argument("--service", default=None)
            item.add_argument("--operation", default="orders.create")
            item.add_argument("--payload", default="{}")
        if command == "serve":
            item.add_argument("--duration", type=float, default=0.0, help="Stop after N seconds; useful for smoke tests")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("target", choices=("config", "state", "journal", "routes", "models"))
    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    get = state_sub.add_parser("get")
    get.add_argument("collection")
    get.add_argument("key")
    query = state_sub.add_parser("query")
    query.add_argument("collection")
    put = state_sub.add_parser("put")
    put.add_argument("collection")
    put.add_argument("key")
    put.add_argument("value")
    state_sub.add_parser("reset")
    snapshot = sub.add_parser("snapshot")
    snapshot_sub = snapshot.add_subparsers(dest="snapshot_command", required=True)
    create = snapshot_sub.add_parser("create")
    create.add_argument("name")
    restore = snapshot_sub.add_parser("restore")
    restore.add_argument("name")
    snapshot_sub.add_parser("list")
    models = sub.add_parser("models")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_sub.add_parser("list")
    models_sub.add_parser("validate")
    plugins = sub.add_parser("plugins")
    plugins_sub = plugins.add_subparsers(dest="plugins_command", required=True)
    plugins_sub.add_parser("list")
    plugins_sub.add_parser("doctor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = init_project(args.root or Path.cwd())
        elif args.command == "validate":
            result = validate_project(args.root)
        elif args.command == "doctor":
            result = doctor(args.root)
        elif args.command in {"compile", "generate"}:
            project = load_project(args.root)
            result = compile_project(project, args.scenario, materialize=args.command == "generate")
        elif args.command == "serve":
            result = serve_project(args.root, args.scenario, args.duration)
        elif args.command == "run":
            result = run_request(args.root, args.scenario, args.service, args.operation, args.payload)
        elif args.command == "inspect":
            result = inspect_project(args.root, args.target)
        elif args.command == "state":
            result = state_command(args.root, args.state_command, args)
        elif args.command == "snapshot":
            result = snapshot_command(args.root, args.snapshot_command, getattr(args, "name", None))
        elif args.command == "models":
            result = models_command(args.root, args.models_command)
        elif args.command == "plugins":
            result = {"plugins": [{"id": "builtin.http", "kinds": ["http", "stream", "filesystem", "mcp"], "modes": ["native"]}]}
        else:
            result = {"status": "ok"}
        _print(result, args.output)
        return 0
    except OmniMockError as exc:
        _print_error(exc, args.output)
        return 4 if exc.category == "contract" else 3 if exc.category in {"configuration", "validation", "security"} else 5
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error = ConfigurationError(ErrorContext("OMC-CLI-001", "Command failed", str(exc)))
        _print_error(error, args.output)
        return 3


def init_project(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("models", "config", "contracts", "scenarios", "fixtures", "schemas", "samples"):
        (directory / name).mkdir(exist_ok=True)
    root = directory / "omnimock.yaml"
    if not root.exists():
        root.write_text(_starter_config(), encoding="utf-8")
    scenario = directory / "scenarios" / "checkout.yaml"
    if not scenario.exists():
        scenario.write_text(_starter_scenario(), encoding="utf-8")
    return {"status": "initialized", "root": str(directory), "created": [str(root), str(scenario)]}


def validate_project(root: Path | None) -> dict[str, Any]:
    project = load_project(root)
    scenario = load_scenario(project)
    profiles = load_profiles(project.root / str(project.raw["config"].get("model_dir", "models")))
    contracts = validate_contracts(project)
    return {"status": "valid", "project": str(project.root), "scenario": scenario.id, "services": len(project.services),
            "model_profiles": [profile.id for profile in profiles], "contracts": contracts}


def doctor(root: Path | None) -> dict[str, Any]:
    project = load_project(root)
    return {"status": "ok", "checks": {
        "loopback_binding": project.runtime_host in {"127.0.0.1", "localhost", "::1"},
        "outbound_network": project.raw["runtime"].get("outbound_network", "deny") == "deny",
        "model_free_default": project.raw["runtime"].get("generation_mode", "materialized") == "materialized",
        "state_driver": project.raw["state"].get("driver", "memory"),
    }}


def compile_project(project: ProjectConfig, scenario_name: str | None, materialize: bool) -> dict[str, Any]:
    scenario = load_scenario(project, scenario_name)
    contracts = validate_contracts(project)
    artifact_dir = project.root / str(project.raw["project"].get("artifact_dir", ".omnimock/artifacts"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = {"artifact_version": "1", "scenario": scenario.id, "scenario_version": scenario.version,
                "seed": scenario.seed, "rules": [rule.id for rule in scenario.rules], "contracts": contracts,
                "materialized": materialize, "generation_mode": "build" if materialize else "materialized"}
    path = artifact_dir / f"{scenario.id}.compiled.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "materialized" if materialize else "compiled", "artifact": str(path), **artifact}


def serve_project(root: Path | None, scenario_name: str | None, duration: float) -> dict[str, Any]:
    project = load_project(root)
    scenario = load_scenario(project, scenario_name)
    runtime = SimulationRuntime(project, scenario)
    servers = []
    for service in project.services:
        if service.mode != "native":
            continue
        listen = service.listen
        port = int(listen.get("port", 0)) if isinstance(listen, dict) else 0
        if port == 0:
            port = 8080 + len(servers)
        sandbox = project.root / (service.root or ".omnimock/mounts/default")
        sandbox.mkdir(parents=True, exist_ok=True)
        servers.append(serve(project, runtime, service.id, project.runtime_host, port, sandbox))
    if not servers:
        raise ConfigurationError(ErrorContext("OMC-RUNTIME-001", "No native services configured"))
    bound = [{"service_id": service.id, "address": f"{project.runtime_host}:{server.server_address[1]}"}
             for service, server in zip((service for service in project.services if service.mode == "native"), servers)]
    if duration > 0:
        time.sleep(duration)
        for server in servers:
            server.shutdown()
        return {"status": "stopped", "endpoints": bound}
    _print({"status": "ready", "endpoints": bound}, "text")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()
        return {"status": "stopped", "endpoints": bound}


def run_request(root: Path | None, scenario_name: str | None, service_id: str | None, operation: str, payload: str) -> Any:
    project = load_project(root)
    scenario = load_scenario(project, scenario_name)
    runtime = SimulationRuntime(project, scenario)
    service = service_id or next((item.id for item in project.services if item.kind == "http"), project.services[0].id)
    result = runtime.request(service, operation, json.loads(payload))
    return {"outcome": result.outcome, "status": result.metadata.get("status"), "payload": result.payload,
            "state_version": result.state_version, "provenance": result.provenance.source}


def inspect_project(root: Path | None, target: str) -> Any:
    project = load_project(root)
    if target == "config":
        return resolved_config(project)
    if target == "models":
        return models_command(root, "list")
    scenario = load_scenario(project)
    runtime = SimulationRuntime(project, scenario)
    if target == "state":
        return runtime.store.read_state()
    if target == "journal":
        return [{"sequence": entry.sequence, "request_id": entry.request_id,
                "operation_id": entry.operation_id, "rule_id": entry.rule_id, "state_version": entry.state_version,
                "fault": entry.fault} for entry in runtime.store.journal()]
    return {"routes": [f"{service.id}: {service.kind}" for service in project.services]}


def state_command(root: Path | None, command: str, args: Any) -> Any:
    project = load_project(root)
    runtime = SimulationRuntime(project, load_scenario(project))
    if command == "get":
        return runtime.store.get_value(args.collection, args.key)
    if command == "query":
        return runtime.store.query(args.collection)
    if command == "put":
        value = json.loads(args.value)
        version = runtime.store.put_value(args.collection, args.key, value)
        runtime._persist()
        return {"status": "updated", "state_version": version}
    if command == "reset":
        reset_scenario = load_scenario(project)
        runtime.store = InMemoryStateStore(reset_scenario.initial_state)
        runtime.engine = BehaviorEngine(reset_scenario, runtime.store)
        runtime._persist()
        return {"status": "reset"}
    return {"status": "ok"}


def snapshot_command(root: Path | None, command: str, name: str | None) -> Any:
    project = load_project(root)
    runtime = SimulationRuntime(project, load_scenario(project))
    if command == "create" and name:
        runtime.snapshot_create(name)
        return {"status": "created", "name": name}
    if command == "restore" and name:
        runtime.snapshot_restore(name)
        return {"status": "restored", "name": name}
    return {"snapshots": runtime.snapshot_names()}


def models_command(root: Path | None, command: str) -> Any:
    project = load_project(root)
    profiles = load_profiles(project.root / str(project.raw["config"].get("model_dir", "models")))
    if command == "validate":
        return {"status": "valid", "count": len(profiles)}
    return {"profiles": [{"id": profile.id, "provider": profile.provider_id, "model": profile.model_id,
                           "capabilities": sorted(profile.capabilities), "digest": profile.digest} for profile in profiles]}


def validate_contracts(project: ProjectConfig) -> list[str]:
    checked: list[str] = []
    for service in project.services:
        if not service.contract:
            continue
        path = project.root / service.contract
        if not path.exists():
            raise ContractError(ErrorContext("OMC-CONTRACT-001", f"Contract not found: {path}", source=str(path)))
        suffix = path.suffix.lower()
        if suffix in {".yaml", ".yml", ".json"}:
            raw = load_document(path)
            if not isinstance(raw, dict):
                raise ContractError(ErrorContext("OMC-CONTRACT-002", "Contract root must be an object", source=str(path)))
            if not any(key in raw for key in ("openapi", "asyncapi", "mcp", "$schema")):
                raise ContractError(ErrorContext("OMC-CONTRACT-003", "Contract type/version is missing", source=str(path)))
        elif suffix in {".graphql", ".proto", ".sql"}:
            if not path.read_text(encoding="utf-8").strip():
                raise ContractError(ErrorContext("OMC-CONTRACT-004", "Contract is empty", source=str(path)))
        checked.append(str(path))
    return checked


def _print(value: Any, output: str) -> None:
    if value is None:
        return
    if output == "json":
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
    elif isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
    else:
        print(value)


def _print_error(error: OmniMockError, output: str) -> None:
    payload = {"code": error.context.code, "message": error.context.public_message}
    if error.context.source:
        payload["source"] = error.context.source
    if output == "json":
        print(json.dumps({"error": payload}, sort_keys=True), file=sys.stderr)
    else:
        print(f"{error.context.code}: {error.context.public_message}{f' ({error.context.source})' if error.context.source else ''}", file=sys.stderr)


def _starter_config() -> str:
    return '''schema_version: "1"
project:
  id: starter
  default_scenario: checkout
runtime:
  host: 127.0.0.1
  control_port: 7777
  strict: true
  seed: 1
  generation_mode: materialized
  outbound_network: deny
config:
  model_dir: models
  scenario_dir: scenarios
state:
  driver: memory
models:
  routing_file: config/model-routing.yaml
services:
  - id: api
    kind: http
    plugin: builtin.http
    mode: native
    state_namespace: default
    listen:
      port: 8080
'''


def _starter_scenario() -> str:
    return '''schema_version: "1"
id: checkout
version: "1.0.0"
seed: 1
initial_state:
  collections:
    orders: {}
rules: []
'''
