from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping
from typing import Any

from .models import RoutingDecision


def safe_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep Codex login auth, but prevent accidental API-key or endpoint switching."""
    env = dict(os.environ if source is None else source)
    env.pop("OPENAI_API_KEY", None)
    env.pop("OPENAI_BASE_URL", None)
    return env


def check_chatgpt_auth(codex_bin: str = "codex", env: Mapping[str, str] | None = None) -> None:
    try:
        result = subprocess.run(
            [codex_bin, "login", "status"],
            capture_output=True,
            text=True,
            timeout=20,
            env=safe_environment(env),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not check Codex authentication: {exc}") from exc
    status = (result.stdout + "\n" + result.stderr).lower()
    if result.returncode != 0 or "logged in using chatgpt" not in status:
        raise RuntimeError(
            "Codex is not using ChatGPT authentication; sign in with `codex login`. cx will not use API-key billing."
        )


def orchestration_prompt(decision: RoutingDecision, task: str) -> str:
    tier = decision.service_tier or "inherited from Codex configuration"
    fork_guidance = (
        ' Use fork_turns="none" to keep child context narrow and allow requested model/effort overrides.'
        if decision.multi_agent_version == "v2"
        else ""
    )
    return (
        "Router execution contract: the selected model is coordinator. Plan the complete task, then delegate only "
        "when at least "
        "two independent, bounded workstreams make delegation worthwhile. Keep at most "
        f"{decision.max_workers} workers total and do not create grandchildren. "
        f"Preferred worker model: {decision.worker_model_id}; "
        f"requested reasoning effort: {decision.worker_effort}. Request those settings when spawning each worker, "
        "but do not claim they took effect unless the runtime confirms them. The coordinator requested service tier "
        f"{tier}; child service tiers cannot be set or confirmed by this router. "
        "Give each worker a narrow context packet."
        f"{fork_guidance} "
        "Default worker tasks to read-only; if edits are necessary, assign non-overlapping file ownership. "
        "The coordinator owns "
        "architecture, integration, final review, and verification. Do not retry an unsuccessful strategy without "
        "explaining what changed.\n\n"
        "User task:\n"
        f"{task}"
    )


def build_interactive_command(
    decision: RoutingDecision,
    task: str,
    *,
    codex_bin: str = "codex",
) -> tuple[list[str], str]:
    prompt = orchestration_prompt(decision, task) if decision.mode == "orchestrated" else task
    command = [codex_bin, "--model", decision.model_id, "-c", f"model_reasoning_effort={decision.effort}"]
    if decision.service_tier:
        command.extend(("-c", f"service_tier={decision.service_tier}"))
    if decision.multi_agent:
        command.extend(("--enable", "multi_agent"))
        if decision.multi_agent_version == "v2":
            command.extend(("--enable", "multi_agent_v2"))
    else:
        command.extend(("--disable", "multi_agent", "--disable", "multi_agent_v2"))
    command.append(prompt)
    return command, prompt


def run_interactive(
    command: list[str],
    *,
    codex_bin: str = "codex",
    env: Mapping[str, str] | None = None,
) -> tuple[int, float]:
    check_chatgpt_auth(codex_bin, env)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, env=safe_environment(env), check=False)
        return completed.returncode, round(time.monotonic() - started, 3)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not start Codex: {exc}") from exc


def find_usage(value: Any) -> dict[str, int] | None:
    """Read the last recognizable token-usage object from Codex JSON events."""
    found: dict[str, int] | None = None

    def visit(node: Any) -> None:
        nonlocal found
        if isinstance(node, dict):
            usage = node.get("usage")
            if isinstance(usage, dict):
                normalized = {
                    key: usage[source]
                    for key, source in (
                        ("input_tokens", "input_tokens"),
                        ("cached_input_tokens", "cached_input_tokens"),
                        ("output_tokens", "output_tokens"),
                        ("reasoning_tokens", "reasoning_tokens"),
                    )
                    if isinstance(usage.get(source), int)
                }
                if normalized:
                    found = normalized
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def parse_usage_events(output: str) -> dict[str, int] | None:
    import json

    usage = None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = find_usage(event)
        if candidate:
            usage = candidate
    return usage
