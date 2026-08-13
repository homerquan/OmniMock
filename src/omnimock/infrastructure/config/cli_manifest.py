from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from omnimock.domain.cli import (
    CliCommandDefinition,
    CliManifest,
    CliManifestLimits,
    CliResponseDefinition,
)
from omnimock.domain.errors import ConfigurationError, ErrorContext
from omnimock.domain.values import JsonValue
from omnimock.infrastructure.config.yaml_loader import load_document

_ARGUMENT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)(?::(path))?\}$")


def load_cli_manifest(project_root: Path, configured_path: str) -> CliManifest:
    path = _project_file(project_root, configured_path)
    document: object = load_document(path)
    raw = _mapping(document, "OMC-CLI-MANIFEST-001", "CLI manifest must be a mapping", path)
    _reject_unknown(
        raw,
        {"$schema", "schema_version", "kind", "id", "limits", "commands"},
        "manifest",
        path,
    )
    if str(raw.get("schema_version", "")) != "1":
        raise _error("OMC-CLI-MANIFEST-002", "Unsupported CLI manifest schema version", path)
    if raw.get("kind") != "cli":
        raise _error("OMC-CLI-MANIFEST-003", "CLI manifest kind must be cli", path)
    manifest_id = str(raw.get("id", "")).strip()
    if not manifest_id:
        raise _error("OMC-CLI-MANIFEST-004", "CLI manifest id is required", path)
    limits = _limits(raw.get("limits", {}), path)
    return CliManifest(manifest_id, _commands(raw.get("commands"), limits, path), limits)


def _project_file(project_root: Path, configured_path: str) -> Path:
    relative = Path(configured_path)
    if relative.is_absolute():
        raise _error("OMC-SECURITY-CLI-001", "CLI manifest path must be relative", project_root)
    root = project_root.resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents:
        raise _error(
            "OMC-SECURITY-CLI-002", "CLI manifest path escapes the project root", project_root
        )
    if not path.is_file():
        raise _error("OMC-CLI-MANIFEST-005", "CLI manifest was not found", path)
    return path


def _limits(value: object, path: Path) -> CliManifestLimits:
    values = _mapping(value, "OMC-CLI-MANIFEST-006", "limits must be a mapping", path)
    _reject_unknown(
        values,
        {"max_arg_count", "max_arg_length", "max_stdout_bytes", "max_stderr_bytes"},
        "limits",
        path,
    )
    return CliManifestLimits(
        _bounded_int(values.get("max_arg_count", 128), 1, 1_000, "max_arg_count", path),
        _bounded_int(values.get("max_arg_length", 4_096), 1, 65_536, "max_arg_length", path),
        _bounded_int(
            values.get("max_stdout_bytes", 1_000_000),
            0,
            10_000_000,
            "max_stdout_bytes",
            path,
        ),
        _bounded_int(
            values.get("max_stderr_bytes", 100_000),
            0,
            1_000_000,
            "max_stderr_bytes",
            path,
        ),
    )


def _commands(
    value: object, limits: CliManifestLimits, path: Path
) -> tuple[CliCommandDefinition, ...]:
    items = _list(value)
    if items is None or not items:
        raise _error("OMC-CLI-MANIFEST-007", "commands must be a non-empty list", path)
    commands: list[CliCommandDefinition] = []
    ids: set[str] = set()
    patterns: set[tuple[str, ...]] = set()
    for item in items:
        raw = _mapping(item, "OMC-CLI-MANIFEST-008", "Every command must be a mapping", path)
        _reject_unknown(raw, {"id", "args", "allow_extra_args", "response"}, "command", path)
        command_id = str(raw.get("id", "")).strip()
        if not command_id or command_id in ids:
            raise _error("OMC-CLI-MANIFEST-009", f"Command id must be unique: {command_id}", path)
        args = _arguments(raw.get("args"), limits, path)
        if args in patterns:
            raise _error(
                "OMC-CLI-MANIFEST-010", "CLI command argument patterns must be unique", path
            )
        allow_extra = raw.get("allow_extra_args", False)
        if not isinstance(allow_extra, bool):
            raise _error("OMC-CLI-MANIFEST-011", "allow_extra_args must be a boolean", path)
        response = _response(raw.get("response"), limits, path)
        commands.append(CliCommandDefinition(command_id, args, response, allow_extra))
        ids.add(command_id)
        patterns.add(args)
    return tuple(commands)


def _arguments(value: object, limits: CliManifestLimits, path: Path) -> tuple[str, ...]:
    items = _list(value)
    if items is None or len(items) > limits.max_arg_count:
        raise _error("OMC-CLI-MANIFEST-012", "command.args exceeds the configured bound", path)
    result: list[str] = []
    captures: set[str] = set()
    for index, item in enumerate(items):
        if (
            not isinstance(item, str)
            or not item
            or len(item) > limits.max_arg_length
            or "\x00" in item
        ):
            raise _error("OMC-CLI-MANIFEST-013", "command.args contains an invalid argument", path)
        parameter = _ARGUMENT.fullmatch(item)
        if "{" in item or "}" in item:
            if parameter is None:
                raise _error("OMC-CLI-MANIFEST-014", "Invalid CLI argument capture", path)
            name, converter = parameter.groups()
            if name in captures or (converter == "path" and index != len(items) - 1):
                raise _error("OMC-CLI-MANIFEST-014", "Invalid CLI argument capture", path)
            captures.add(name)
        result.append(item)
    return tuple(result)


def _response(value: object, limits: CliManifestLimits, path: Path) -> CliResponseDefinition:
    raw = _mapping(value, "OMC-CLI-MANIFEST-015", "command.response must be a mapping", path)
    _reject_unknown(raw, {"exit_code", "stdout", "stderr"}, "command.response", path)
    exit_code = _bounded_int(raw.get("exit_code", 0), 0, 255, "response.exit_code", path)
    stdout = _json_value(raw.get("stdout", ""), path)
    stderr_value = raw.get("stderr", "")
    if not isinstance(stderr_value, str):
        raise _error("OMC-CLI-MANIFEST-016", "response.stderr must be a string", path)
    if len(_output_bytes(stdout)) > limits.max_stdout_bytes:
        raise _error("OMC-CLI-MANIFEST-017", "response.stdout exceeds max_stdout_bytes", path)
    if len(stderr_value.encode()) > limits.max_stderr_bytes:
        raise _error("OMC-CLI-MANIFEST-018", "response.stderr exceeds max_stderr_bytes", path)
    return CliResponseDefinition(exit_code, stdout, stderr_value)


def _output_bytes(value: JsonValue) -> bytes:
    if isinstance(value, str):
        return value.encode()
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _bounded_int(value: object, minimum: int, maximum: int, label: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _error("OMC-CLI-MANIFEST-019", f"{label} must be an integer", path)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _error("OMC-CLI-MANIFEST-019", f"{label} must be an integer", path) from exc
    if not minimum <= parsed <= maximum:
        raise _error(
            "OMC-CLI-MANIFEST-020", f"{label} must be between {minimum} and {maximum}", path
        )
    return parsed


def _reject_unknown(value: Mapping[str, object], allowed: set[str], label: str, path: Path) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise _error(
            "OMC-CLI-MANIFEST-021", f"Unknown {label} field(s): {', '.join(unknown)}", path
        )


def _mapping(value: object, code: str, message: str, path: Path) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _error(code, message, path)
    source = cast(Mapping[object, object], value)
    result: dict[str, object] = {}
    for key, item in source.items():
        if not isinstance(key, str):
            raise _error(code, message, path)
        result[key] = item
    return result


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None


def _json_value(value: object, path: Path) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    items = _list(value)
    if items is not None:
        return [_json_value(item, path) for item in items]
    if isinstance(value, Mapping):
        mapping = _mapping(
            cast(object, value),
            "OMC-CLI-MANIFEST-022",
            "CLI response data must be valid JSON",
            path,
        )
        return {key: _json_value(item, path) for key, item in mapping.items()}
    raise _error("OMC-CLI-MANIFEST-022", "CLI response data must be valid JSON", path)


def _error(code: str, message: str, path: Path) -> ConfigurationError:
    return ConfigurationError(ErrorContext(code, message, source=str(path)))
