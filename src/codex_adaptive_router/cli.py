from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from . import __version__
from .config import ConfigStore, effective_config, load_config, set_config_value
from .discovery import (
    approve_model,
    discover_bundled_catalog,
    mark_evaluated,
    read_registry,
    set_model_state,
    sync_registry,
    write_registry,
)
from .evaluation import run_evaluation, validate_evaluation_request
from .execution import build_interactive_command, check_chatgpt_auth, run_interactive
from .metrics import append_event, config_directory, new_run_id, read_events, state_directory, update_feedback, utc_now
from .models import ModelCapability, RoutingDecision
from .policy import route_task

MANAGEMENT_COMMANDS = {"models", "config", "doctor", "explain", "evaluate", "status", "feedback", "update"}
HELP = """cx — local, model-agnostic Codex CLI routing

Usage:
  cx [--mode auto|direct|luna|sol|orchestrated] [options] "task"
  cx models [refresh|approve|disable|enable ...]
  cx config [show|init|set|reset|rollback ...]
  cx doctor
  cx explain "task"
  cx evaluate --models ID[,ID] [--mode direct|orchestrated] [--fast] --confirm-usage [--cases FILE]
  cx status
  cx feedback RUN_ID success|failure [--verified]

Run options: --model ID, --effort LEVEL, --fast, --standard, --dry-run, --explain
No task text or Codex output is stored in local metrics.
"""


def parse_run_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cx", description="Route a task to a Codex CLI model.")
    parser.add_argument("--mode", choices=("auto", "direct", "luna", "sol", "orchestrated"), default="auto")
    parser.add_argument("--model", help="explicit model ID from the current Codex catalog")
    parser.add_argument("--effort", help="explicit reasoning effort supported by the selected model")
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument("--fast", action="store_true", help="request Fast independently of reasoning effort")
    tier.add_argument("--standard", action="store_true", help="override inherited Fast with the standard tier")
    parser.add_argument("--dry-run", action="store_true", help="explain the route without launching Codex")
    parser.add_argument("--explain", action="store_true", help="alias for --dry-run")
    parser.add_argument("--version", action="version", version=f"cx {__version__}")
    parser.add_argument("task", nargs="+", metavar="TASK")
    args = parser.parse_args(list(argv))
    args.task = " ".join(args.task)
    return args


def _paths(env: dict[str, str] | None = None) -> tuple[Path, Path]:
    return config_directory(env) / "config.json", state_directory(env)


def _codex_bin(env: dict[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    selected = values.get("CX_CODEX_BIN") or shutil.which("codex")
    if not selected:
        raise RuntimeError("Codex CLI was not found on PATH")
    return selected


def _codex_version(binary: str) -> str | None:
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _metadata_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _catalog_freshness(metadata: dict, config: dict) -> str:
    discovered = _metadata_time(metadata.get("discovered_at"))
    if discovered is None:
        return "unknown"
    age = max(timedelta(0), datetime.now(UTC) - discovered)
    status = "stale" if age >= timedelta(days=config["discovery"]["stale_days"]) else "fresh"
    return f"{status} ({age.days} days old; stale after {config['discovery']['stale_days']} days)"


def _load_registry(
    state_dir: Path,
    config: dict,
    codex_bin: str,
    *,
    force_refresh: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[dict[str, ModelCapability], dict]:
    path = state_dir / "registry.json"
    registry, metadata = read_registry(path)
    discovered = _metadata_time(metadata.get("discovered_at"))
    refresh_hours = config["discovery"]["refresh_hours"]
    due = force_refresh or discovered is None or datetime.now(UTC) - discovered >= timedelta(hours=refresh_hours)
    if due:
        try:
            catalog, raw = discover_bundled_catalog(codex_bin)
            if len(raw.encode()) > config["discovery"]["max_catalog_bytes"]:
                raise RuntimeError("bundled Codex catalog exceeds the configured size limit")
            now = utc_now()
            registry = sync_registry(catalog, registry, now)
            metadata = {
                "discovered_at": now,
                "catalog_hash": hashlib.sha256(raw.encode()).hexdigest(),
                "codex_version": _codex_version(codex_bin),
                "source": "codex debug models --bundled",
                "account_availability_source": "not exposed by the bundled model catalog",
            }
            write_registry(path, registry, metadata)
        except (RuntimeError, OSError, ValueError) as exc:
            if not registry:
                raise RuntimeError(f"model discovery failed and no previous registry is available: {exc}") from exc
            print(f"cx: warning: discovery failed; using the last-known registry: {exc}", file=sys.stderr)
    return registry, metadata


def _save_registry(state_dir: Path, registry: dict[str, ModelCapability], metadata: dict) -> None:
    write_registry(state_dir / "registry.json", registry, metadata)


def _mode_name(mode: str) -> str:
    return {"luna": "Luna Direct", "sol": "Sol Direct", "orchestrated": "Sol Orchestrated"}.get(mode, mode)


def _tier_name(tier: str | None) -> str:
    return {"fast": "Fast (requested)", "default": "Standard (requested)", None: "Inherit Codex setting"}[tier]


def render_decision(decision: RoutingDecision) -> str:
    lines = [
        f"Selected mode: {_mode_name(decision.mode)}",
        f"Model: {decision.model_id}",
        f"Reasoning effort: {decision.effort}",
        f"Service tier: {_tier_name(decision.service_tier)}",
        f"Confidence: {decision.confidence:.0%} (heuristic, not a success probability)",
    ]
    if decision.multi_agent:
        lines.extend(
            (
                f"Workers: up to {decision.max_workers}",
                f"Preferred worker: {decision.worker_model_id} / {decision.worker_effort} "
                "(requested; runtime may not confirm)",
            )
        )
    lines.extend((f"Reason: {decision.reason}", "Signals:"))
    for name, value in (
        ("difficulty", decision.signals.difficulty),
        ("ambiguity", decision.signals.ambiguity),
        ("scope", decision.signals.scope),
        ("risk", decision.signals.risk),
        ("verification ease", decision.signals.verification_ease),
        ("parallelism", decision.signals.parallelism),
        ("coordination overhead", decision.signals.coordination_overhead),
        ("latency sensitivity", decision.signals.latency_sensitivity),
    ):
        lines.append(f"  {name}: {value:.2f}")
    lines.append(f"Verification: {decision.verification}")
    return "\n".join(lines)


def _run_task(args: argparse.Namespace, env: dict[str, str] | None = None) -> int:
    config_path, state_dir = _paths(env)
    config = effective_config(load_config(config_path))
    codex_bin = _codex_bin(env)
    registry, metadata = _load_registry(state_dir, config, codex_bin, env=env)
    decision = route_task(
        args.task,
        config,
        registry,
        mode_override=args.mode,
        model_override=args.model,
        effort_override=args.effort,
        fast_override=args.fast,
        standard_override=args.standard,
    )
    if args.dry_run or args.explain:
        print(render_decision(decision))
        return 0

    command, _ = build_interactive_command(decision, args.task, codex_bin=codex_bin)
    run_id = new_run_id()
    print(f"cx route {run_id}: {_mode_name(decision.mode)} · {decision.model_id} · {decision.effort}", file=sys.stderr)
    try:
        exit_code, elapsed = run_interactive(command, codex_bin=codex_bin, env=env)
    except RuntimeError as exc:
        append_event(
            state_dir / "runs.jsonl",
            {
                "run_id": run_id,
                "timestamp": utc_now(),
                "task_category": "router_or_auth_error",
                "mode": decision.mode,
                "model_id": decision.model_id,
                "effort": decision.effort,
                "service_tier_requested": decision.service_tier,
                "actual_service_tier": None,
                "duration_seconds": 0,
                "codex_exit_code": None,
                "usage": None,
                "worker_count_observed": None,
                "task_outcome": "unknown",
                "verification_result": "unknown",
                "retry_count": 0,
                "escalation_count": 0,
                "error_category": "authentication_or_launch",
            },
        )
        print(f"cx: {exc}", file=sys.stderr)
        return 2

    append_event(
        state_dir / "runs.jsonl",
        {
            "run_id": run_id,
            "timestamp": utc_now(),
            "task_category": "read_only" if decision.signals.read_only else "coding_or_reasoning",
            "mode": decision.mode,
            "model_id": decision.model_id,
            "effort": decision.effort,
            "service_tier_requested": decision.service_tier,
            "actual_service_tier": None,
            "duration_seconds": elapsed,
            "codex_exit_code": exit_code,
            "usage": None,
            "worker_model_requested": decision.worker_model_id,
            "worker_count_limit": decision.max_workers if decision.multi_agent else 0,
            "worker_count_observed": None,
            "task_outcome": "unknown",
            "verification_result": "unknown",
            "retry_count": 0,
            "escalation_count": 0,
            "manual_override": decision.manual_override,
            "routing_confidence": decision.confidence,
            "discovery_hash": metadata.get("catalog_hash"),
        },
    )
    print(f"\nRun {run_id} returned exit code {exit_code}; task success remains unverified.", file=sys.stderr)
    print(f"After checking the result: cx feedback {run_id} success --verified", file=sys.stderr)
    return exit_code


def _models_command(argv: Sequence[str], env: dict[str, str] | None) -> int:
    parser = argparse.ArgumentParser(prog="cx models")
    parser.add_argument("action", nargs="?", choices=("refresh", "approve", "disable", "enable"), default="list")
    parser.add_argument("model_id", nargs="?")
    args = parser.parse_args(list(argv))
    _, state_dir = _paths(env)
    config = load_config(_paths(env)[0])
    binary = _codex_bin(env)
    refresh = args.action == "refresh" or args.action in {"approve", "disable", "enable"}
    registry, metadata = _load_registry(state_dir, config, binary, force_refresh=refresh, env=env)
    if args.action in {"approve", "disable", "enable"}:
        if not args.model_id:
            parser.error(f"models {args.action} requires a model ID")
        if args.action == "approve":
            registry = approve_model(registry, args.model_id)
        else:
            registry = set_model_state(registry, args.model_id, "disabled" if args.action == "disable" else "untested")
        _save_registry(state_dir, registry, metadata)
        print(f"{args.model_id}: {registry[args.model_id].lifecycle}")
        return 0
    if args.model_id:
        parser.error("models list and refresh do not take a model ID")
    if args.action == "refresh":
        print(
            f"Discovered {len(registry)} catalog models at {metadata.get('discovered_at')} "
            "(bundled; no account entitlement check)."
        )
    for item in sorted(registry.values(), key=lambda row: row.model_id):
        efforts = ",".join(item.reasoning_efforts) or "unknown"
        tiers = ",".join(item.service_tiers)
        agents = item.multi_agent_version or "no"
        availability = item.account_availability
        print(
            f"{item.model_id}\t{item.lifecycle}\teffort={efforts}\ttier={tiers}"
            f"\tmulti_agent={agents}\taccount={availability}"
        )
    print(
        f"Catalog source: {metadata.get('source') or 'not discovered'}; "
        f"last discovery: {metadata.get('discovered_at') or 'never'}"
    )
    print(f"Catalog freshness: {_catalog_freshness(metadata, config)}")
    return 0


def _config_command(argv: Sequence[str], env: dict[str, str] | None) -> int:
    parser = argparse.ArgumentParser(prog="cx config")
    parser.add_argument("action", nargs="?", choices=("show", "init", "set", "reset", "rollback"), default="show")
    parser.add_argument("key", nargs="?")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(list(argv))
    config_path, state_dir = _paths(env)
    store = ConfigStore(config_path, state_dir / "config-history")
    config = store.load()
    if args.action == "show":
        note = " (defaults; run cx config init to write)" if not config_path.exists() else ""
        print(f"Config path: {config_path}{note}")
        print(json.dumps(config, indent=2, sort_keys=True))
    elif args.action == "init":
        if config_path.exists():
            print(f"Config already exists: {config_path}")
            return 0
        store.save(config)
        print(f"Initialized {config_path}")
    elif args.action == "set":
        if not args.key or args.value is None:
            parser.error("config set requires KEY VALUE")
        store.save(set_config_value(config, args.key, args.value))
        print(f"Updated {args.key}; previous versions are in {state_dir / 'config-history'}")
    elif args.action == "reset":
        store.reset()
        print(f"Reset to defaults: {config_path}")
    elif args.action == "rollback":
        store.rollback()
        print(f"Restored the previous configuration: {config_path}")
    return 0


def _doctor(env: dict[str, str] | None) -> int:
    config_path, state_dir = _paths(env)
    config = load_config(config_path)
    binary = _codex_bin(env)
    version = _codex_version(binary)
    print(f"Codex CLI: {version or 'unavailable'}")
    auth_ok = True
    try:
        check_chatgpt_auth(binary, env)
        print("Authentication: ChatGPT login detected; API-key environment overrides are ignored by cx")
    except RuntimeError as exc:
        auth_ok = False
        print(f"Authentication: {exc}")
    try:
        registry, metadata = _load_registry(state_dir, config, binary, env=env)
        configured = [model_id for ids in config["models"]["roles"].values() for model_id in ids]
        missing = [model_id for model_id in configured if model_id not in registry]
        unapproved = [
            model_id for model_id in configured if model_id in registry and registry[model_id].lifecycle != "approved"
        ]
        print(f"Bundled model catalog: {len(registry)} models; discovered {metadata.get('discovered_at') or 'never'}")
        print(f"Catalog freshness: {_catalog_freshness(metadata, config)}")
        print(f"Configured profiles absent from catalog: {', '.join(missing) if missing else 'none'}")
        print(f"Configured profiles needing evaluation/approval: {', '.join(unapproved) if unapproved else 'none'}")
    except RuntimeError as exc:
        print(f"Model discovery: {exc}")
        return 1
    try:
        features = subprocess.run(
            [binary, "features", "list"], capture_output=True, text=True, timeout=30, check=False, env=env
        )
        feature_rows = {}
        for line in features.stdout.splitlines():
            columns = line.split()
            if len(columns) >= 3 and columns[0] in {"multi_agent", "multi_agent_v2"}:
                feature_rows[columns[0]] = f"{columns[1]}, default {'on' if columns[2] == 'true' else 'off'}"
        v1 = feature_rows.get("multi_agent", "not listed")
        v2 = feature_rows.get("multi_agent_v2", "not listed")
        print(
            f"Native multi-agent: v1 {v1}; v2 {v2}. cx enables the catalogued version only for orchestrated sessions."
        )
    except (OSError, subprocess.SubprocessError):
        print("Native multi-agent: could not inspect")
    print(f"Router config: {config_path}")
    print(f"Router state: {state_dir}")
    return 0 if auth_ok else 1


def _explain(argv: Sequence[str], env: dict[str, str] | None) -> int:
    parser = argparse.ArgumentParser(prog="cx explain")
    parser.add_argument("task", nargs="+")
    args = parser.parse_args(list(argv))
    run_args = argparse.Namespace(
        task=" ".join(args.task),
        mode="auto",
        model=None,
        effort=None,
        fast=False,
        standard=False,
        dry_run=True,
        explain=False,
    )
    return _run_task(run_args, env)


def _evaluate(argv: Sequence[str], env: dict[str, str] | None) -> int:
    parser = argparse.ArgumentParser(prog="cx evaluate")
    parser.add_argument("--models", required=True, help="comma-separated model IDs")
    parser.add_argument("--mode", choices=("direct", "orchestrated"), default="direct")
    parser.add_argument("--cases", help="JSON evaluation cases (defaults to the packaged smoke case)")
    parser.add_argument("--effort")
    parser.add_argument("--worker-effort")
    parser.add_argument("--fast", action="store_true", help="request Fast for the coordinator")
    parser.add_argument("--confirm-usage", action="store_true")
    args = parser.parse_args(list(argv))
    model_ids = list(dict.fromkeys(item.strip() for item in args.models.split(",") if item.strip()))
    try:
        default_cases = (
            "orchestration_evaluation_cases.json" if args.mode == "orchestrated" else "evaluation_cases.json"
        )
        case_text = (
            Path(args.cases).expanduser().read_text(encoding="utf-8")
            if args.cases
            else files("codex_adaptive_router").joinpath(default_cases).read_text(encoding="utf-8")
        )
        cases = json.loads(case_text)
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read evaluation cases: {exc}")
    config_path, state_dir = _paths(env)
    config = load_config(config_path)
    validate_evaluation_request(
        model_ids,
        cases,
        confirmed=args.confirm_usage,
        max_models=config["evaluation"]["max_models"],
        max_cases=config["evaluation"]["max_cases"],
    )
    binary = _codex_bin(env)
    registry, metadata = _load_registry(state_dir, config, binary, env=env)
    selected: list[ModelCapability] = []
    for model_id in model_ids:
        model = registry.get(model_id)
        if not model:
            parser.error(f"model is not in the current bundled catalog: {model_id}")
        if model.lifecycle in {"disabled", "deprecated"}:
            parser.error(f"model is {model.lifecycle}: {model_id}")
        selected.append(model)
    worker_model = None
    worker_effort = None
    if args.mode == "orchestrated":
        for model_id in config["models"]["roles"]["worker"]:
            candidate = registry.get(model_id)
            if candidate and candidate.lifecycle == "approved" and candidate.reasoning_efforts:
                worker_model = candidate
                break
        if worker_model is None:
            parser.error("orchestrated evaluation needs an approved worker model")
        worker_effort = args.worker_effort or config["efforts"]["worker"]
        try:
            if worker_effort not in worker_model.reasoning_efforts:
                parser.error(f"worker model {worker_model.model_id} does not support effort {worker_effort}")
        except ValueError as exc:
            parser.error(str(exc))
        if any(not model.supports_multi_agent for model in selected):
            parser.error("every orchestrated evaluation model must be catalogued as multi-agent capable")
    service_tier = "fast" if args.fast else "default"
    if any(service_tier not in model.service_tiers for model in selected):
        parser.error(f"service tier {service_tier} is not advertised by every selected model")
    results = run_evaluation(
        selected,
        cases,
        codex_bin=binary,
        timeout=config["evaluation"]["timeout_seconds"],
        effort=args.effort,
        mode=args.mode,
        worker_model=worker_model,
        worker_effort=worker_effort,
        service_tier=service_tier,
    )
    summaries = {row["model_id"]: row for row in results if row.get("event") == "model_evaluation_summary"}
    for model_id, summary in summaries.items():
        registry = mark_evaluated(
            registry,
            model_id,
            success=summary["passed"],
            available=summary["account_available"],
            verified_at=utc_now(),
        )
    _save_registry(state_dir, registry, metadata)
    for result in results:
        if result.get("event") == "model_evaluation_summary":
            continue
        append_event(
            state_dir / "evaluations.jsonl",
            {
                "timestamp": utc_now(),
                "event": "evaluation_case",
                **result,
            },
        )
        print(
            f"{result['model_id']} / {result['case_id']}: {'PASS' if result['passed'] else 'FAIL'}; "
            f"mode={result['execution_mode']}; verification={result['verification']}; "
            f"exit={result['process_exit_code']}; "
            f"{result['duration_seconds']:.1f}s; usage={result['usage'] or 'not reported'}"
            + (
                f"; worker={result['worker_model_requested']}/{result['worker_effort_requested']} "
                "requested, count=unknown"
                if result["execution_mode"] == "orchestrated"
                else ""
            )
        )
    for model_id, summary in summaries.items():
        outcome = (
            "evaluated; eligible for manual approval" if summary["passed"] else "evaluated; not eligible for approval"
        )
        print(f"{model_id}: {outcome}")
    return 0 if summaries and all(row["passed"] for row in summaries.values()) else 1


def _status(env: dict[str, str] | None) -> int:
    _, state_dir = _paths(env)
    events = read_events(state_dir / "runs.jsonl")[-10:]
    if not events:
        print("No routed runs recorded.")
        print(f"State directory: {state_dir}")
        return 0
    for item in events:
        print(
            f"{item.get('run_id')}\t{item.get('timestamp')}\t{item.get('mode')}\t{item.get('model_id')}"
            f"/{item.get('effort')}\texit={item.get('codex_exit_code')}\toutcome={item.get('task_outcome')}"
        )
    print("Interactive task outcomes remain unknown until you record verified feedback.")
    return 0


def _feedback(argv: Sequence[str], env: dict[str, str] | None) -> int:
    parser = argparse.ArgumentParser(prog="cx feedback")
    parser.add_argument("run_id")
    parser.add_argument("outcome", choices=("success", "failure"))
    parser.add_argument("--verified", action="store_true")
    args = parser.parse_args(list(argv))
    _, state_dir = _paths(env)
    try:
        update_feedback(state_dir / "runs.jsonl", args.run_id, args.outcome, args.verified)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    verification = (
        "passed"
        if args.verified and args.outcome == "success"
        else "unverified"
        if args.outcome == "success"
        else "failed"
    )
    print(f"Recorded {args.outcome}; verification={verification}")
    return 0


def _update() -> int:
    source = Path(__file__).resolve().parents[2]
    try:
        root = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        root = None
    if root and root.returncode == 0:
        print(f"Source checkout: {root.stdout.strip()}")
        print("Update with: git pull --ff-only")
        print("The cx command does not download or replace code automatically.")
    else:
        print("Upgrade with the package manager used to install cx; see the installation guide.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)
    if not values or values[0] in {"-h", "--help", "help"}:
        print(HELP, end="")
        return 0
    command, *rest = values
    try:
        if command == "models":
            return _models_command(rest, env)
        if command == "config":
            return _config_command(rest, env)
        if command == "doctor":
            if rest:
                raise ValueError("cx doctor takes no arguments")
            return _doctor(env)
        if command == "explain":
            return _explain(rest, env)
        if command == "evaluate":
            return _evaluate(rest, env)
        if command == "status":
            if rest:
                raise ValueError("cx status takes no arguments")
            return _status(env)
        if command == "feedback":
            return _feedback(rest, env)
        if command == "update":
            return _update()
        args = parse_run_args(values)
        return _run_task(args, env)
    except (ValueError, RuntimeError, KeyError, PermissionError, OSError) as exc:
        print(f"cx: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
