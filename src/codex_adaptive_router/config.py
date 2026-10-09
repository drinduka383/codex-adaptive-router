from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any


_DEFAULT_CONFIG: dict[str, Any] = json.loads(
    files("codex_adaptive_router").joinpath("defaults.json").read_text(encoding="utf-8")
)


def default_config() -> dict[str, Any]:
    return copy.deepcopy(_DEFAULT_CONFIG)


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_nested(value: Any, schema: Any, path: str) -> None:
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        for key, item in value.items():
            if key not in schema:
                raise ValueError(f"unknown config key: {path}.{key}")
            _validate_nested(item, schema[key], f"{path}.{key}")
    elif not isinstance(value, type(schema)) and not (
        isinstance(schema, float) and isinstance(value, int) and not isinstance(value, bool)
    ):
        raise ValueError(f"{path} must be {type(schema).__name__}")


def _validate_config_shape(value: dict[str, Any]) -> None:
    for key, item in value.items():
        if key not in _DEFAULT_CONFIG:
            raise ValueError(f"unknown config key: {key}")
        if key != "presets":
            _validate_nested(item, _DEFAULT_CONFIG[key], key)
            continue
        if not isinstance(item, dict):
            raise ValueError("presets must be an object")
        override_schema = {
            name: schema
            for name, schema in _DEFAULT_CONFIG.items()
            if name not in {"version", "preset", "presets"}
        }
        for preset_name, overrides in item.items():
            if not isinstance(overrides, dict):
                raise ValueError(f"presets.{preset_name} must be an object")
            _validate_nested(overrides, override_schema, f"presets.{preset_name}")


def validate_config(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("config version must be 1")
    _validate_config_shape(value)
    roles = value.get("models", {}).get("roles", {})
    for role in ("efficient", "capable", "worker"):
        ids = roles.get(role)
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids):
            raise ValueError(f"models.roles.{role} must be a non-empty list of model IDs")
    for name in (
        "confidence_min",
        "strong_model_difficulty",
        "strong_model_risk",
        "orchestration_scope",
        "orchestration_parallelism",
        "coordination_overhead_max",
    ):
        number = value.get("routing", {}).get(name)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 1:
            raise ValueError(f"routing.{name} must be between 0 and 1")
    workers = value.get("routing", {}).get("max_workers")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 6:
        raise ValueError("routing.max_workers must be an integer from 1 through 6")
    for section, key, maximum in (
        ("routing", "max_task_chars", 20000),
        ("discovery", "refresh_hours", 8760),
        ("discovery", "stale_days", 3650),
        ("discovery", "max_catalog_bytes", 50000000),
        ("evaluation", "max_cases", 5),
        ("evaluation", "max_models", 4),
        ("evaluation", "timeout_seconds", 300),
    ):
        item = value[section][key]
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= maximum:
            raise ValueError(f"{section}.{key} must be an integer from 1 through {maximum}")
    for name in ("max_retries", "max_escalations"):
        count = value.get("routing", {}).get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count != 0:
            raise ValueError(f"routing.{name} must remain 0 until verified escalation is implemented")
    if value.get("service_tier", {}).get("fast_mode") not in {"auto", "always", "never", "inherit"}:
        raise ValueError("service_tier.fast_mode must be auto, always, never, or inherit")
    if value.get("preset") not in value.get("presets", {}):
        raise ValueError("preset must name a configured preset")


def effective_config(value: dict[str, Any]) -> dict[str, Any]:
    validate_config(value)
    preset = value.get("presets", {}).get(value.get("preset"), {})
    return _merge(value, preset)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_config()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read router config {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("router config must be a JSON object")
    merged = _merge(_DEFAULT_CONFIG, loaded)
    validate_config(merged)
    return merged


def set_config_value(value: dict[str, Any], dotted_key: str, raw_value: str) -> dict[str, Any]:
    keys = dotted_key.split(".")
    if not keys or any(not key for key in keys):
        raise KeyError(dotted_key)
    updated = copy.deepcopy(value)
    node: Any = updated
    for key in keys[:-1]:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(dotted_key)
        node = node[key]
    if not isinstance(node, dict) or keys[-1] not in node:
        raise KeyError(dotted_key)
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        parsed = raw_value
    old = node[keys[-1]]
    same_numeric_family = isinstance(old, float) and isinstance(parsed, int) and not isinstance(parsed, bool)
    if old is not None and not isinstance(parsed, type(old)) and not same_numeric_family:
        raise ValueError(f"value for {dotted_key} must be {type(old).__name__}")
    node[keys[-1]] = parsed
    validate_config(updated)
    return updated


def _secure_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class ConfigStore:
    def __init__(self, path: Path, history_dir: Path):
        self.path = path
        self.history_dir = history_dir

    def load(self) -> dict[str, Any]:
        return load_config(self.path)

    def save(self, value: dict[str, Any]) -> None:
        validate_config(value)
        if self.path.exists():
            self.history_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.history_dir, 0o700)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            backup = self.history_dir / f"{stamp}.json"
            backup.write_bytes(self.path.read_bytes())
            os.chmod(backup, 0o600)
            backups = sorted(self.history_dir.glob("*.json"))
            for old in backups[:-20]:
                old.unlink()
        _secure_write(self.path, json.dumps(value, indent=2, sort_keys=True) + "\n")

    def rollback(self) -> dict[str, Any]:
        backups = sorted(self.history_dir.glob("*.json")) if self.history_dir.exists() else []
        if not backups:
            raise FileNotFoundError("no config history is available")
        selected = backups[-1]
        value = load_config(selected)
        if self.path.exists():
            self.history_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            current = self.history_dir / f"{stamp}.json"
            current.write_bytes(self.path.read_bytes())
            os.chmod(current, 0o600)
        _secure_write(self.path, json.dumps(value, indent=2, sort_keys=True) + "\n")
        selected.unlink()
        return value

    def reset(self) -> dict[str, Any]:
        value = default_config()
        self.save(value)
        return value
