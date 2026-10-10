from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shlex
import shutil
import socket
import stat
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
_CX_STATUS_MESSAGE = "cx is checking for an explicit $cx request"
_OLD_CX_STATUS_MESSAGE = "cx is selecting a model for this turn"
_CX_PREFIXES = ("$cx", "$codex-adaptive-router:cx")
# shortcut: one pending handoff per session for 30s; use turn IDs if concurrent queued prompts are added.
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


def mark_handoff(
    state_dir: Path,
    session_id: str,
    prompt: str,
    decision: RoutingDecision,
    *,
    now: float | None = None,
) -> None:
    created = time.time() if now is None else now
    _write_private_json(
        _handoff_path(state_dir, session_id),
        {
            "prompt_hash": _prompt_hash(prompt),
            "expires_at": created + _HANDOFF_TTL_SECONDS,
            "model_id": decision.model_id,
            "effort": decision.effort,
            "service_tier": decision.service_tier,
        },
    )


def consume_handoff(
    state_dir: Path,
    session_id: str,
    prompt: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    path = _handoff_path(state_dir, session_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    current = time.time() if now is None else now
    if payload.get("expires_at", 0) < current:
        path.unlink(missing_ok=True)
        return None
    if payload.get("prompt_hash") != _prompt_hash(prompt):
        return None
    path.unlink(missing_ok=True)
    return payload


def clear_handoff(state_dir: Path, session_id: str) -> None:
    _handoff_path(state_dir, session_id).unlink(missing_ok=True)


def _queue_returncode(result: Any) -> int:
    return result.returncode if hasattr(result, "returncode") else int(result)


def _app_server_socket_paths(env: dict[str, str] | None = None) -> list[Path]:
    values = os.environ if env is None else env
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise RuntimeError("Live thread routing requires a Unix Codex app-server.")
    uid = getuid()
    socket_root = Path(values.get("TMPDIR") or tempfile.gettempdir()).expanduser() / f"codex-daemon-{uid}"
    try:
        root_info = socket_root.lstat()
    except OSError as exc:
        raise RuntimeError("No running Codex app-server socket was found.") from exc
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != uid or stat.S_IMODE(root_info.st_mode) & 0o077:
        raise RuntimeError("The Codex app-server socket directory is not private; refusing to use it.")
    sockets = []
    for path in socket_root.iterdir():
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == uid:
            sockets.append(path)
    if not sockets:
        raise RuntimeError("No running Codex app-server socket was found.")
    return sockets[:8]


def apply_thread_settings(
    thread_id: str,
    decision: RoutingDecision,
    *,
    env: dict[str, str] | None = None,
) -> bool:
    last_error = None
    for path in _app_server_socket_paths(env):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5)
        buffered = bytearray()

        def read_exact(size: int) -> bytes:
            while len(buffered) < size:
                chunk = connection.recv(max(4096, size - len(buffered)))
                if not chunk:
                    raise RuntimeError("Codex closed the app-server connection.")
                buffered.extend(chunk)
            data = bytes(buffered[:size])
            del buffered[:size]
            return data

        def send_frame(payload: bytes, opcode: int = 1) -> None:
            mask = secrets.token_bytes(4)
            size = len(payload)
            if size < 126:
                header = bytes((0x80 | opcode, 0x80 | size))
            elif size < 65_536:
                header = bytes((0x80 | opcode, 0x80 | 126)) + size.to_bytes(2, "big")
            else:
                header = bytes((0x80 | opcode, 0x80 | 127)) + size.to_bytes(8, "big")
            connection.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

        def receive_message() -> bytes:
            fragments = bytearray()
            fragment_opcode = None
            while True:
                first, second = read_exact(2)
                final = bool(first & 0x80)
                opcode = first & 0x0F
                size = second & 0x7F
                if size == 126:
                    size = int.from_bytes(read_exact(2), "big")
                elif size == 127:
                    size = int.from_bytes(read_exact(8), "big")
                if size > 1_048_576:
                    raise RuntimeError("Codex sent an oversized app-server response.")
                mask = read_exact(4) if second & 0x80 else None
                payload = read_exact(size)
                if mask:
                    payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
                if opcode == 8:
                    raise RuntimeError("Codex closed the app-server connection.")
                if opcode == 9:
                    send_frame(payload, 10)
                    continue
                if opcode in (1, 2):
                    fragments = bytearray(payload)
                    fragment_opcode = opcode
                elif opcode == 0 and fragment_opcode is not None:
                    fragments.extend(payload)
                else:
                    continue
                if final:
                    if fragment_opcode != 1:
                        raise RuntimeError("Codex sent a non-text app-server response.")
                    return bytes(fragments)

        def send_message(message: dict[str, Any]) -> None:
            send_frame(json.dumps(message, separators=(",", ":")).encode())

        def receive_response(request_id: int, method: str) -> dict[str, Any]:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                connection.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    response = json.loads(receive_message())
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Codex sent invalid JSON over the app-server socket.") from exc
                if not isinstance(response, dict) or response.get("id") != request_id:
                    continue
                if "error" in response:
                    code = (
                        response["error"].get("code", "unknown") if isinstance(response["error"], dict) else "unknown"
                    )
                    raise RuntimeError(f"Codex rejected {method} (RPC error {code}).")
                if "result" not in response:
                    raise RuntimeError(f"Codex returned no result for {method}.")
                return response
            raise RuntimeError(f"Codex did not acknowledge {method}.")

        try:
            connection.connect(str(path))
            key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
            connection.sendall(
                (
                    "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n"
                ).encode()
            )
            while b"\r\n\r\n" not in buffered:
                chunk = connection.recv(4096)
                if not chunk:
                    raise RuntimeError("Codex closed the app-server connection during WebSocket handshake.")
                buffered.extend(chunk)
                if len(buffered) > 16_384:
                    raise RuntimeError("Codex returned oversized WebSocket headers.")
            end = buffered.index(b"\r\n\r\n") + 4
            headers = bytes(buffered[:end])
            del buffered[:end]
            lines = headers.split(b"\r\n")
            parsed_headers = {
                line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip().decode("ascii", "ignore")
                for line in lines[1:]
                if b":" in line
            }
            expected_accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode("ascii")
            accepted = parsed_headers.get(b"sec-websocket-accept") == expected_accept
            if not lines[0].startswith(b"HTTP/1.1 101 ") or not accepted:
                raise RuntimeError("Codex rejected the app-server WebSocket handshake.")

            send_message(
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "codex-adaptive-router",
                            "title": "Codex Adaptive Router",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            receive_response(1, "initialize")
            send_message({"method": "initialized", "params": {}})
            send_message(
                {
                    "method": "thread/settings/update",
                    "id": 2,
                    "params": {
                        "threadId": thread_id,
                        "model": decision.model_id,
                        "effort": decision.effort,
                        **({"serviceTier": decision.service_tier} if decision.service_tier else {}),
                    },
                }
            )
            receive_response(2, "thread/settings/update")
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            last_error = (
                str(exc) if isinstance(exc, RuntimeError) else f"Codex socket unavailable ({type(exc).__name__})."
            )
        finally:
            connection.close()

    raise RuntimeError(last_error or "Codex did not apply the selected thread settings.")


def _cx_task(prompt: str) -> str | None:
    text = prompt.lstrip()
    for prefix in _CX_PREFIXES:
        if text == prefix:
            return ""
        if text.startswith(prefix) and len(text) > len(prefix) and text[len(prefix)].isspace():
            return text[len(prefix) :].strip()
    return None


def handle_prompt_submit(
    event: Any,
    *,
    state_dir: Path,
    route_for_task,
    update_thread_settings,
    queue,
    codex_bin: str = "codex",
) -> HookOutcome:
    if not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit":
        return HookOutcome(None, None, "ignored")
    session_id = event.get("session_id")
    prompt = event.get("prompt")
    if not isinstance(session_id, str) or not session_id or not isinstance(prompt, str) or not prompt.strip():
        return HookOutcome(None, None, "ignored")
    handoff = consume_handoff(state_dir, session_id, prompt)
    if handoff is not None:
        expected_model = handoff.get("model_id")
        reported_model = event.get("model")
        effort = handoff.get("effort", "unknown")
        tier = {"fast": "Fast", "default": "Standard"}.get(handoff.get("service_tier"), "Codex default")
        if isinstance(reported_model, str) and reported_model:
            if reported_model == expected_model:
                message = (
                    f"cx confirmed active model {reported_model}; requested effort {effort} · requested tier {tier}. "
                    "Only the model is confirmed by this hook."
                )
            else:
                message = (
                    f"cx route mismatch: requested model {expected_model}, but Codex reports active model "
                    f"{reported_model}; the explicit request is blocked (requested effort {effort} · "
                    f"requested tier {tier})."
                )
        else:
            message = (
                f"cx queued {expected_model} · {effort} · {tier}, but Codex's active model was not reported; "
                "the explicit request is blocked because the route is unverified."
            )
        response = {"systemMessage": message}
        if not isinstance(reported_model, str) or not reported_model or reported_model != expected_model:
            response.update(
                decision="block",
                reason="cx could not verify the requested model for this explicit route.",
            )
        return HookOutcome(response, None, "handoff")

    task = _cx_task(prompt)
    if task is None:
        return HookOutcome(None, None, "not_routed")
    if not task:
        return HookOutcome(
            {
                "decision": "block",
                "reason": "$cx needs a task to route.",
                "systemMessage": "Usage: $cx <task>",
            },
            None,
            "empty_cx",
        )

    try:
        decision = route_for_task(task)
    except (OSError, RuntimeError, ValueError, KeyError):
        return HookOutcome(
            {
                "decision": "block",
                "reason": "cx could not select a route for this explicit request.",
                "systemMessage": "cx could not select a route; this explicit request is blocked. Check `cx doctor` "
                "and `cx models refresh`, then retry with `$cx <task>`.",
            },
            None,
            "route_failed",
        )

    try:
        command = build_queue_command(decision, task, session_id, codex_bin=codex_bin)
    except (OSError, ValueError):
        return HookOutcome(
            {
                "decision": "block",
                "reason": "cx could not prepare this explicit routed request.",
                "systemMessage": "cx could not prepare the routed turn; this explicit request is blocked. "
                "Check `cx doctor`, then retry with `$cx <task>`.",
            },
            decision,
            "queue_failed",
        )
    update_error = None
    try:
        updated = update_thread_settings(session_id, decision)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        updated = False
        update_error = str(exc)
    if not updated:
        detail = update_error or "Codex did not confirm the per-thread settings update."
        return HookOutcome(
            {
                "decision": "block",
                "reason": "cx could not apply the selected model to this conversation.",
                "systemMessage": (
                    f"cx could not apply model {decision.model_id} to the active thread ({detail}); the explicit "
                    "request was not queued."
                ),
            },
            decision,
            "settings_update_failed",
        )
    try:
        queued_prompt = command[command.index("--message") + 1]
        mark_handoff(state_dir, session_id, queued_prompt, decision)
    except (OSError, ValueError):
        return HookOutcome(
            {
                "decision": "block",
                "reason": "cx could not prepare this explicit routed request.",
                "systemMessage": "cx could not prepare the routed turn; this explicit request is blocked. "
                "Check `cx doctor`, then retry with `$cx <task>`.",
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
        message = (
            f"cx queued route {decision.model_id} · requested effort {decision.effort} · requested tier {tier}; "
            "awaiting active-model confirmation"
        )
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
            "decision": "block",
            "reason": "cx could not queue this explicit routed request.",
            "systemMessage": "cx could not queue the routed turn; this explicit request is blocked. "
            "Check `cx doctor`, then retry with `$cx <task>`.",
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
        or hook.get("statusMessage") not in {_CX_STATUS_MESSAGE, _OLD_CX_STATUS_MESSAGE}
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
    for group in event:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for hook in group["hooks"]:
            if not _is_cx_hook(hook):
                continue
            if hook.get("command") == command and hook.get("statusMessage") == _CX_STATUS_MESSAGE:
                return False, None
            backup = _backup_config(hooks_path)
            hook["command"] = command
            hook["statusMessage"] = _CX_STATUS_MESSAGE
            _atomic_write(hooks_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            return True, backup
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
