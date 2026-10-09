from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def state_directory(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    base = values.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "codex-adaptive-router"


def config_directory(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    base = values.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "codex-adaptive-router"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            result.append(row)
    return result


def update_feedback(path: Path, run_id: str, outcome: str, verified: bool) -> None:
    if outcome not in {"success", "failure"}:
        raise ValueError("outcome must be success or failure")
    events = read_events(path)
    match = next((item for item in reversed(events) if item.get("run_id") == run_id), None)
    if match is None:
        raise KeyError(f"unknown run ID: {run_id}")
    match["task_outcome"] = outcome
    match["verification_result"] = (
        "passed" if verified and outcome == "success" else "failed" if outcome == "failure" else "unverified"
    )
    match["feedback_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)
