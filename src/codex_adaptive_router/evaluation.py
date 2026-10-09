from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .execution import check_chatgpt_auth, parse_usage_events, safe_environment
from .models import ModelCapability


def validate_evaluation_request(
    model_ids: list[str],
    cases: list[dict[str, Any]],
    *,
    confirmed: bool,
    max_models: int = 4,
    max_cases: int = 5,
) -> None:
    if not confirmed:
        raise PermissionError("evaluation may consume Codex allowance; rerun with --confirm-usage")
    if not 1 <= len(model_ids) <= max_models:
        raise ValueError(f"evaluation requires 1 through {max_models} model IDs")
    if not 1 <= len(cases) <= max_cases:
        raise ValueError(f"evaluation requires 1 through {max_cases} cases")
    for case in cases:
        if not isinstance(case, dict) or not all(
            isinstance(case.get(key), str) and case[key].strip() for key in ("id", "prompt", "expected_regex")
        ):
            raise ValueError("each evaluation case needs non-empty id, prompt, and expected_regex strings")
        if len(case["prompt"]) > 8000:
            raise ValueError("evaluation prompt exceeds 8000 characters")
        try:
            re.compile(case["expected_regex"])
        except re.error as exc:
            raise ValueError(f"invalid expected_regex in case {case['id']}: {exc}") from exc


def evaluation_effort(model: ModelCapability, requested: str | None = None) -> str:
    if requested:
        if requested not in model.reasoning_efforts:
            raise ValueError(f"model {model.model_id} does not support effort {requested}")
        return requested
    if "low" in model.reasoning_efforts:
        return "low"
    if model.default_effort in model.reasoning_efforts:
        return model.default_effort or "low"
    if not model.reasoning_efforts:
        raise ValueError(f"model {model.model_id} has no discovered reasoning effort")
    return model.reasoning_efforts[0]


def build_evaluation_command(
    model: ModelCapability,
    effort: str,
    case: dict[str, str],
    output_file: Path,
    *,
    codex_bin: str = "codex",
    mode: str = "direct",
    worker_model_id: str | None = None,
    worker_effort: str | None = None,
    service_tier: str = "default",
) -> list[str]:
    prompt = (
        "This is an opt-in, read-only router evaluation. Do not edit files, run commands that modify state, or access "
        "external services. Follow this task and return only the requested result:\n\n"
        + case["prompt"]
    )
    if mode not in {"direct", "orchestrated"}:
        raise ValueError("evaluation mode must be direct or orchestrated")
    if service_tier not in model.service_tiers:
        raise ValueError(f"model {model.model_id} does not advertise service tier {service_tier}")
    if mode == "orchestrated":
        if not model.supports_multi_agent:
            raise ValueError(f"model {model.model_id} is not catalogued as multi-agent capable")
        if not worker_model_id or not worker_effort:
            raise ValueError("orchestrated evaluation requires a worker model and effort")
        fork_guidance = (
            ' Use fork_turns="none" for narrow V2 child context.' if model.multi_agent_version == "v2" else ""
        )
        prompt = (
            "Read-only orchestration evaluation. Do not edit files, run modifying commands, "
            "or access external services. "
            "Use no more than one worker total and do not create grandchildren. Delegate one independent part only if "
            f"useful; request worker model {worker_model_id} with reasoning_effort {worker_effort}.{fork_guidance} "
            "Root owns integration and checks the expected result.\n\n"
            + prompt
        )
    command = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--model",
        model.model_id,
        "-c",
        f"model_reasoning_effort={effort}",
        "-c",
        f"service_tier={service_tier}",
    ]
    if mode == "orchestrated":
        command.extend(("--enable", "multi_agent"))
        if model.multi_agent_version == "v2":
            command.extend(("--enable", "multi_agent_v2"))
    else:
        command.extend(("--disable", "multi_agent", "--disable", "multi_agent_v2"))
    command.extend(("--output-last-message", str(output_file), prompt))
    return command


def run_evaluation(
    models: list[ModelCapability],
    cases: list[dict[str, str]],
    *,
    codex_bin: str = "codex",
    timeout: int = 300,
    effort: str | None = None,
    mode: str = "direct",
    worker_model: ModelCapability | None = None,
    worker_effort: str | None = None,
    service_tier: str = "default",
) -> list[dict[str, Any]]:
    if mode not in {"direct", "orchestrated"}:
        raise ValueError("evaluation mode must be direct or orchestrated")
    if mode == "orchestrated" and worker_model is None:
        raise ValueError("orchestrated evaluation requires an approved worker model")
    if mode == "orchestrated" and worker_model is not None:
        if not worker_model.reasoning_efforts:
            raise ValueError(f"worker model {worker_model.model_id} has no supported reasoning effort")
        if worker_effort not in worker_model.reasoning_efforts:
            raise ValueError(f"worker model {worker_model.model_id} does not support effort {worker_effort}")
        if any(not model.supports_multi_agent for model in models):
            raise ValueError("every orchestrated evaluation model must be multi-agent capable")
    if any(service_tier not in model.service_tiers for model in models):
        raise ValueError(f"service tier {service_tier} is not advertised by every selected model")
    check_chatgpt_auth(codex_bin)
    results: list[dict[str, Any]] = []
    for model in models:
        model_passed = True
        model_available = True
        for case in cases:
            selected_effort = evaluation_effort(model, effort)
            with tempfile.TemporaryDirectory(prefix="cx-eval-") as temp:
                output_file = Path(temp) / "last-message.txt"
                command = build_evaluation_command(
                    model,
                    selected_effort,
                    case,
                    output_file,
                    codex_bin=codex_bin,
                    mode=mode,
                    worker_model_id=worker_model.model_id if worker_model else None,
                    worker_effort=worker_effort,
                    service_tier=service_tier,
                )
                started = time.monotonic()
                try:
                    process = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        env=safe_environment(),
                        timeout=timeout,
                        check=False,
                    )
                    elapsed = round(time.monotonic() - started, 3)
                    response = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
                    matched = process.returncode == 0 and (
                        re.search(case["expected_regex"], response, re.IGNORECASE | re.DOTALL) is not None
                    )
                    model_available = model_available and process.returncode == 0
                    model_passed = model_passed and matched
                    usage = parse_usage_events(process.stdout)
                    verification = (
                        "process_exit"
                        if process.returncode != 0
                        else "missing_last_message"
                        if not output_file.exists()
                        else "expected_regex_not_matched"
                        if not matched
                        else "expected_regex_passed"
                    )
                    results.append({
                        "case_id": case["id"],
                        "model_id": model.model_id,
                        "execution_mode": mode,
                        "worker_model_requested": (
                            worker_model.model_id if worker_model and mode == "orchestrated" else None
                        ),
                        "worker_effort_requested": worker_effort if mode == "orchestrated" else None,
                        "worker_count_observed": None,
                        "effort": selected_effort,
                        "service_tier_requested": service_tier,
                        "duration_seconds": elapsed,
                        "process_exit_code": process.returncode,
                        "passed": matched,
                        "usage": usage,
                        "verification": verification,
                    })
                except subprocess.TimeoutExpired:
                    model_available = False
                    model_passed = False
                    results.append({
                        "case_id": case["id"],
                        "model_id": model.model_id,
                        "execution_mode": mode,
                        "worker_model_requested": (
                            worker_model.model_id if worker_model and mode == "orchestrated" else None
                        ),
                        "worker_effort_requested": worker_effort if mode == "orchestrated" else None,
                        "worker_count_observed": None,
                        "effort": selected_effort,
                        "service_tier_requested": service_tier,
                        "duration_seconds": float(timeout),
                        "process_exit_code": None,
                        "passed": False,
                        "usage": None,
                        "verification": "timed_out",
                    })
        results.append({
            "event": "model_evaluation_summary",
            "model_id": model.model_id,
            "passed": model_passed,
            "account_available": model_available,
        })
    return results
