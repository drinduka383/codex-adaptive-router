from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .execution import orchestration_prompt
from .models import RoutingDecision

_BEGIN = "# BEGIN CODEX ADAPTIVE ROUTER (cx)"
_END = "# END CODEX ADAPTIVE ROUTER (cx)"
_CX_STATUS_MESSAGE = "cx is selecting a model for this turn"
# shortcut: one hash-only handoff per session for 30s; use turn IDs if concurrent queued prompts are added.
_HANDOFF_TTL_SECONDS = 30


@dataclass(frozen=True)
class HookOutcome:
    response: dict[str, str] | None
    decision: RoutingDecision | None
    status: str
    duration_seconds: float = 0.0
    queued: bool = False


def codex_config_path(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    root = Path(values.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return root / "config.toml"


def codex_hooks_path(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    root = Path(values.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return root / "hooks.json"


def build_queue_command(
    decision: RoutingDecision,
    task: str,
    thread_id: str,
    *,
    codex_bin: str = "codex",
) -> list[str]:
    if not thread_id or len(thread_id) > 256:
        raise ValueError("hook event did not contain a valid session ID")
    prompt = orchestration_prompt(decision, task) if decision.mode == "orchestrated" else task
    command = [
        codex_bin,
        "queue",
        "--remote",
        "unix://",
        "--thread",
        thread_id,
        "--message",
        prompt,
        "--model",
        decision.model_id,
        "-c",
        f"model_reasoning_effort={decision.effort}",
    ]
    if decision.service_tier:
        command.extend(("-c", f"service_tier={decision.service_tier}"))
    if decision.multi_agent:
        command.extend(("--enable", "multi_agent"))
        if decision.multi_agent_version == "v2":
            command.extend(("--enable", "multi_agent_v2"))
    else:
        command.extend(("--disable", "multi_agent", "--disable", "multi_agent_v2"))
    return command


def _handoff_path(state_dir: Path, session_id: str) -> Path:
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return state_dir / "pending-handoffs" / f"{key}.json"


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mark_handoff(state_dir: Path, session_id: str, prompt: str, *, now: float | None = None) -> None:
    created = time.time() if now is None else now
    _write_private_json(
        _handoff_path(state_dir, session_id),
        {"prompt_hash": _prompt_hash(prompt), "expires_at": created + _HANDOFF_TTL_SECONDS},
    )


def consume_handoff(state_dir: Path, session_id: str, prompt: str, *, now: float | None = None) -> bool:
    path = _handoff_path(state_dir, session_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    current = time.time() if now is None else now
    if payload.get("expires_at", 0) < current:
        path.unlink(missing_ok=True)
        return False
    if payload.get("prompt_hash") != _prompt_hash(prompt):
        return False
    path.unlink(missing_ok=True)
    return True


def clear_handoff(state_dir: Path, session_id: str) -> None:
    _handoff_path(state_dir, session_id).unlink(missing_ok=True)


def _queue_returncode(result: Any) -> int:
    return result.returncode if hasattr(result, "returncode") else int(result)


def handle_prompt_submit(
    event: Any,
    *,
    state_dir: Path,
    route_for_task,
    queue,
    codex_bin: str = "codex",
) -> HookOutcome:
    if not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit":
        return HookOutcome(None, None, "ignored")
    session_id = event.get("session_id")
    prompt = event.get("prompt")
    if not isinstance(session_id, str) or not session_id or not isinstance(prompt, str) or not prompt.strip():
        return HookOutcome(None, None, "ignored")
    if consume_handoff(state_dir, session_id, prompt):
        return HookOutcome(None, None, "handoff")

    try:
        decision = route_for_task(prompt)
    except (OSError, RuntimeError, ValueError, KeyError):
        return HookOutcome(
            {
                "systemMessage": "cx routing is unavailable; check `cx doctor` and `cx models refresh`. "
                "Letting the original prompt continue on the current model."
            },
            None,
            "route_failed",
        )

    try:
        command = build_queue_command(decision, prompt, session_id, codex_bin=codex_bin)
        mark_handoff(state_dir, session_id, prompt)
    except (OSError, ValueError):
        return HookOutcome(
            {
                "systemMessage": "cx could not prepare the routed turn; letting the original prompt continue "
                "on the current model."
            },
            decision,
            "queue_failed",
        )
    started = time.monotonic()
    try:
        returncode = _queue_returncode(queue(command))
    except (OSError, subprocess.SubprocessError, ValueError):
        returncode = 1
    elapsed = round(time.monotonic() - started, 3)
    if returncode == 0:
        tier = {"fast": "Fast", "default": "Standard"}.get(decision.service_tier, "Codex default")
        message = f"cx routed this turn to {decision.model_id} · {decision.effort} · {tier}"
        return HookOutcome(
            {
                "decision": "block",
                "reason": "cx queued this prompt on the current conversation.",
                "systemMessage": message,
            },
            decision,
            "queued",
            elapsed,
            True,
        )

    clear_handoff(state_dir, session_id)
    return HookOutcome(
        {
            "systemMessage": "cx could not queue the routed turn; letting the original prompt continue on the "
            "current model."
        },
        decision,
        "queue_failed",
        elapsed,
        False,
    )


def _backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.cx-backup-{stamp}")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.cx-backup-{stamp}-{suffix}")
        suffix += 1
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    return backup


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _is_cx_hook(hook: Any) -> bool:
    if (
        not isinstance(hook, dict)
        or hook.get("type") != "command"
        or hook.get("statusMessage") != _CX_STATUS_MESSAGE
        or not isinstance(hook.get("command"), str)
    ):
        return False
    try:
        command = shlex.split(hook["command"])
    except ValueError:
        return False
    return len(command) == 2 and Path(command[0]).name == "cx" and command[1] == "hook-submit"


def _read_hooks_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot safely edit Codex hooks file: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Codex hooks file must contain a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Codex hooks file must contain a hooks object")
    return data


def prompt_hook_installed(hooks_path: Path) -> bool:
    event = _read_hooks_file(hooks_path)["hooks"].get("UserPromptSubmit", [])
    return isinstance(event, list) and any(
        isinstance(group, dict) and any(_is_cx_hook(hook) for hook in group.get("hooks", [])) for group in event
    )


def legacy_prompt_hook_installed(config_path: Path) -> bool:
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return _BEGIN in text and _END in text


def install_prompt_hook(hooks_path: Path, command: str) -> tuple[bool, Path | None]:
    data = _read_hooks_file(hooks_path)
    event = data["hooks"].setdefault("UserPromptSubmit", [])
    if not isinstance(event, list):
        raise ValueError("Codex UserPromptSubmit hooks must be a JSON array")
    if any(isinstance(group, dict) and any(_is_cx_hook(hook) for hook in group.get("hooks", [])) for group in event):
        return False, None
    event.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 60,
                    "statusMessage": _CX_STATUS_MESSAGE,
                }
            ]
        }
    )
    backup = _backup_config(hooks_path)
    _atomic_write(hooks_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True, backup


def remove_prompt_hook(hooks_path: Path) -> tuple[bool, Path | None]:
    data = _read_hooks_file(hooks_path)
    event = data["hooks"].get("UserPromptSubmit")
    if not isinstance(event, list):
        return False, None
    updated_event = []
    removed = False
    for group in event:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            updated_event.append(group)
            continue
        handlers = [hook for hook in group["hooks"] if not _is_cx_hook(hook)]
        if len(handlers) == len(group["hooks"]):
            updated_event.append(group)
            continue
        removed = True
        if handlers:
            updated_event.append({**group, "hooks": handlers})
        elif set(group) != {"hooks"}:
            updated_event.append({**group, "hooks": []})
    if not removed:
        return False, None
    if updated_event:
        data["hooks"]["UserPromptSubmit"] = updated_event
    else:
        data["hooks"].pop("UserPromptSubmit")
    backup = _backup_config(hooks_path)
    _atomic_write(hooks_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True, backup


def remove_legacy_prompt_hook(config_path: Path) -> tuple[bool, Path | None]:
    try:
        current = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, None
    start = current.find(_BEGIN)
    end = current.find(_END)
    if start < 0 and end < 0:
        return False, None
    if start < 0 or end < start:
        raise ValueError("Codex config contains an incomplete cx hook block")
    end += len(_END)
    if current[end : end + 1] == "\n":
        end += 1
    if start > 0 and current[start - 1 : start] == "\n":
        start -= 1
    updated = current[:start] + current[end:]
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"removing the cx hook would make Codex config invalid: {exc}") from exc
    backup = _backup_config(config_path)
    _atomic_write(config_path, updated)
    return True, backup
