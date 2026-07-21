from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from omnimock.domain.errors import ConfigurationError, ErrorContext
from omnimock.infrastructure.config.yaml_loader import load_document


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: str
    provider_id: str
    adapter: str
    model_id: str
    capabilities: frozenset[str]
    digest: str
    source: Path


def load_profiles(directory: Path) -> tuple[ModelProfile, ...]:
    if not directory.exists():
        return ()
    profiles: list[ModelProfile] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        raw = _load_json(path)
        if not isinstance(raw, Mapping):
            raise ConfigurationError(ErrorContext("OMC-MODEL-001", "Model profile must be an object", source=str(path)))
        required = ("schema_version", "id", "provider", "model", "runtime", "privacy")
        missing = [field for field in required if field not in raw]
        if missing:
            raise ConfigurationError(ErrorContext("OMC-MODEL-002", f"Model profile missing: {', '.join(missing)}", source=str(path)))
        if str(raw["schema_version"]) != "1":
            raise ConfigurationError(ErrorContext("OMC-MODEL-003", "Unsupported model profile schema version", source=str(path)))
        profile_id = str(raw["id"])
        if profile_id in seen:
            raise ConfigurationError(ErrorContext("OMC-MODEL-004", f"Duplicate model profile id: {profile_id}", source=str(path)))
        seen.add(profile_id)
        provider = raw["provider"]
        model = raw["model"]
        if not isinstance(provider, Mapping) or not isinstance(model, Mapping):
            raise ConfigurationError(ErrorContext("OMC-MODEL-005", "provider and model must be objects", source=str(path)))
        capabilities = model.get("capabilities", {})
        if not isinstance(capabilities, Mapping):
            raise ConfigurationError(ErrorContext("OMC-MODEL-006", "model.capabilities must be an object", source=str(path)))
        digest = "sha256:" + hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        profiles.append(ModelProfile(profile_id, str(provider.get("id", "")), str(provider.get("adapter", "")),
                                     str(model.get("id", "")), frozenset(str(k) for k, v in capabilities.items() if v), digest, path))
    return tuple(profiles)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(ErrorContext("OMC-MODEL-007", f"Invalid model profile: {path}", str(exc), str(path))) from exc

