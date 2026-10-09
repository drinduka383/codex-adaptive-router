from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ModelCapability


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_catalog(raw: str) -> dict[str, ModelCapability]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Codex model catalog is not valid JSON: {exc}") from exc
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Codex model catalog must contain a models list")
    parsed: dict[str, ModelCapability] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("slug"), str):
            continue
        model_id = row["slug"]
        efforts = tuple(
            str(item["effort"])
            for item in row.get("supported_reasoning_levels", [])
            if isinstance(item, dict) and isinstance(item.get("effort"), str)
        )
        tier_rows: list[tuple[str, str]] = []
        tiers = {"default"}
        for item in row.get("service_tiers", []):
            if not isinstance(item, dict):
                continue
            tier_id = str(item.get("id", ""))
            name = str(item.get("name", tier_id))
            if tier_id:
                tier_rows.append((tier_id, name))
                if tier_id.lower() in {"priority", "fast"} or name.lower() == "fast":
                    tiers.add("fast")
                else:
                    tiers.add(tier_id)
        parsed[model_id] = ModelCapability(
            model_id=model_id,
            display_name=str(row.get("display_name") or model_id),
            reasoning_efforts=efforts,
            service_tiers=tuple(sorted(tiers)),
            multi_agent_version=row.get("multi_agent_version"),
            default_effort=row.get("default_reasoning_level"),
            catalog_service_tiers=tuple(tier_rows),
        )
    if not parsed:
        raise ValueError("Codex model catalog contained no usable model entries")
    return parsed


def sync_registry(
    catalog: dict[str, ModelCapability],
    previous: dict[str, dict[str, Any] | ModelCapability],
    now: str,
) -> dict[str, ModelCapability]:
    updated: dict[str, ModelCapability] = {}
    for model_id, discovered in catalog.items():
        old_value = previous.get(model_id)
        old = (
            old_value
            if isinstance(old_value, ModelCapability)
            else ModelCapability.from_dict(old_value)
            if old_value
            else None
        )
        if old is None:
            state = "discovered"
            account_status = "unknown"
            evaluation_passed = None
            first_seen = now
            verified = None
            evidence: tuple[str, ...] = ("bundled Codex model catalog",)
        else:
            state = old.lifecycle
            account_status = old.account_availability
            evaluation_passed = old.evaluation_passed
            first_seen = old.first_seen_at or now
            verified = old.last_verified_at
            evidence = old.evidence
            changed = any((
                old.display_name != discovered.display_name,
                old.reasoning_efforts != discovered.reasoning_efforts,
                old.service_tiers != discovered.service_tiers,
                old.multi_agent_version != discovered.multi_agent_version,
                old.default_effort != discovered.default_effort,
                old.catalog_service_tiers != discovered.catalog_service_tiers,
            ))
            if changed:
                evidence += ("catalog capabilities changed; prior evaluation must be repeated",)
                if state != "disabled":
                    state = "untested"
                    account_status = "unknown"
                    evaluation_passed = None
                    verified = None
            elif state in {"discovered", "deprecated"}:
                state = "untested"
        updated[model_id] = ModelCapability(
            model_id=discovered.model_id,
            display_name=discovered.display_name,
            reasoning_efforts=discovered.reasoning_efforts,
            service_tiers=discovered.service_tiers,
            multi_agent_version=discovered.multi_agent_version,
            default_effort=discovered.default_effort,
            catalog_service_tiers=discovered.catalog_service_tiers,
            lifecycle=state,
            account_availability=account_status,
            evaluation_passed=evaluation_passed,
            first_seen_at=first_seen,
            last_seen_at=now,
            last_verified_at=verified,
            evidence=evidence,
        )
    for model_id, old_value in previous.items():
        if model_id in updated:
            continue
        old = old_value if isinstance(old_value, ModelCapability) else ModelCapability.from_dict(old_value)
        if old.lifecycle != "disabled":
            old = ModelCapability.from_dict(
                {**old.to_dict(), "lifecycle": "deprecated", "last_seen_at": old.last_seen_at}
            )
        updated[model_id] = old
    return updated


def mark_evaluated(
    registry: dict[str, ModelCapability],
    model_id: str,
    *,
    success: bool,
    verified_at: str,
    available: bool | None = None,
) -> dict[str, ModelCapability]:
    if model_id not in registry:
        raise KeyError(f"model is not in the discovered catalog: {model_id}")
    result = dict(registry)
    model = result[model_id]
    result[model_id] = ModelCapability.from_dict({
        **model.to_dict(),
        "lifecycle": "evaluated" if model.lifecycle != "approved" else "approved",
        "account_availability": (
            "observed_available" if (success if available is None else available) else "observed_unavailable"
        ),
        "evaluation_passed": success,
        "last_verified_at": verified_at,
        "evidence": list(model.evidence)
        + (["opt-in read-only Codex evaluation passed"] if success else ["opt-in Codex evaluation failed"]),
    })
    return result


def approve_model(registry: dict[str, ModelCapability], model_id: str) -> dict[str, ModelCapability]:
    if model_id not in registry:
        raise KeyError(f"model is not in the discovered catalog: {model_id}")
    model = registry[model_id]
    if (
        model.lifecycle != "evaluated"
        or model.account_availability != "observed_available"
        or model.evaluation_passed is not True
    ):
        raise ValueError("a model must pass an opt-in evaluation before it can be approved")
    result = dict(registry)
    result[model_id] = ModelCapability.from_dict({**model.to_dict(), "lifecycle": "approved"})
    return result


def set_model_state(registry: dict[str, ModelCapability], model_id: str, state: str) -> dict[str, ModelCapability]:
    if model_id not in registry:
        raise KeyError(f"model is not in the discovered catalog: {model_id}")
    if state not in {"disabled", "untested"}:
        raise ValueError("manual state changes support only disabled or untested")
    result = dict(registry)
    result[model_id] = ModelCapability.from_dict({**result[model_id].to_dict(), "lifecycle": state})
    return result


def read_registry(path: Path) -> tuple[dict[str, ModelCapability], dict[str, Any]]:
    if not path.exists():
        return {}, {"discovered_at": None, "catalog_hash": None, "codex_version": None, "source": None}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("models", {})
    return {key: ModelCapability.from_dict(value) for key, value in rows.items()}, {
        key: payload.get(key) for key in ("discovered_at", "catalog_hash", "codex_version", "source")
    }


def write_registry(path: Path, registry: dict[str, ModelCapability], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    payload = {**metadata, "models": {key: value.to_dict() for key, value in sorted(registry.items())}}
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def discover_bundled_catalog(codex_bin: str = "codex", timeout: int = 60) -> tuple[dict[str, ModelCapability], str]:
    try:
        result = subprocess.run(
            [codex_bin, "debug", "models", "--bundled"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not read Codex's bundled model catalog: {exc}") from exc
    return parse_catalog(result.stdout), result.stdout
