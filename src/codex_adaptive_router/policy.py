from __future__ import annotations

from .classifier import classify_task
from .config import effective_config, validate_config
from .models import ModelCapability, RoutingDecision, TaskSignals

_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")


def _eligible(model: ModelCapability, *, automatic: bool) -> bool:
    if model.lifecycle in {"disabled", "deprecated"}:
        return False
    if automatic and model.lifecycle != "approved":
        return False
    return bool(model.reasoning_efforts)


def _select_model(
    role: str,
    config: dict,
    registry: dict[str, ModelCapability],
    *,
    automatic: bool,
    override: str | None = None,
) -> ModelCapability:
    if override:
        candidate = registry.get(override)
        if not candidate:
            raise ValueError(f"model is not present in the current Codex catalog: {override}")
        if not _eligible(candidate, automatic=False):
            raise ValueError(f"model is disabled, deprecated, or has no reasoning capabilities: {override}")
        return candidate

    roles = config["models"]["roles"]
    preference_ids = list(roles.get(role, []))
    if role == "efficient":
        preference_ids.extend(roles.get("capable", []))
    preference_ids.extend(config["models"].get("fallback_order", []))
    seen: set[str] = set()
    for model_id in preference_ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        candidate = registry.get(model_id)
        if candidate and _eligible(candidate, automatic=automatic):
            return candidate
    raise ValueError(
        f"no approved model is available for the {role} role; run `cx models`, evaluate a model, then approve it"
    )


def _effort_for(model: ModelCapability, preferred: str) -> str:
    if preferred in model.reasoning_efforts:
        return preferred
    if not model.reasoning_efforts:
        raise ValueError(f"model {model.model_id} has no discovered reasoning efforts")
    desired = _EFFORT_ORDER.index(preferred) if preferred in _EFFORT_ORDER else 0
    available = sorted(
        set(model.reasoning_efforts),
        key=lambda effort: _EFFORT_ORDER.index(effort) if effort in _EFFORT_ORDER else 0,
    )
    # Use the nearest advertised effort; prefer the stronger one on an equal-distance tie.
    return min(
        available,
        key=lambda effort: (
            abs((_EFFORT_ORDER.index(effort) if effort in _EFFORT_ORDER else 0) - desired),
            -(_EFFORT_ORDER.index(effort) if effort in _EFFORT_ORDER else 0),
        ),
    )


def _mode_for(signals: TaskSignals, config: dict, *, allow_orchestration: bool) -> str:
    rules = config["routing"]
    if signals.confidence < rules["confidence_min"]:
        return "sol"
    if (
        signals.risk >= rules["strong_model_risk"]
        or signals.difficulty >= 0.82
        or (signals.expected_interactions >= 0.72 and signals.ambiguity >= 0.45)
    ):
        return "sol"
    if (
        allow_orchestration
        and rules["max_workers"] >= 2
        and signals.independent_tasks >= 2
        and signals.scope >= rules["orchestration_scope"]
        and signals.parallelism >= rules["orchestration_parallelism"]
        and signals.coordination_overhead <= rules["coordination_overhead_max"]
    ):
        return "orchestrated"
    if signals.scope >= rules["orchestration_scope"]:
        return "sol"
    if signals.difficulty >= rules["strong_model_difficulty"] or signals.ambiguity >= 0.58:
        return "sol"
    return "luna"


def _preferred_effort(mode: str, task: str, signals: TaskSignals, config: dict) -> str:
    efforts = config["efforts"]
    text = task.lower()
    if mode == "orchestrated":
        return efforts["sol_high"] if signals.difficulty >= 0.68 else efforts["orchestrator"]
    if mode == "luna":
        if signals.read_only or any(
            word in text for word in ("document", "readme", "documentation", "inspect", "list", "explain")
        ):
            return efforts["luna_inspection"]
        if any(word in text for word in ("rename", "reformat", "boilerplate", "typo", "mechanical", "update imports")):
            return efforts["luna_mechanical"]
        if signals.difficulty >= 0.68:
            return efforts["sol_high"]
        return efforts["luna_general"]
    if signals.difficulty >= 0.86 or signals.risk >= 0.92:
        return efforts["sol_very_high"]
    if signals.difficulty >= 0.64 or signals.risk >= 0.78:
        return efforts["sol_high"]
    if signals.difficulty >= 0.42 or signals.ambiguity >= 0.48:
        return efforts["sol_medium"]
    return efforts["sol_low"]


def _tier_for(
    model: ModelCapability,
    signals: TaskSignals,
    config: dict,
    *,
    fast_override: bool,
    standard_override: bool,
    mode: str,
) -> str | None:
    if standard_override:
        return "default"
    policy = config["service_tier"]
    fast_mode = policy["fast_mode"]
    wants_fast = fast_override or fast_mode == "always"
    if not wants_fast and fast_mode == "auto":
        wants_fast = (
            signals.latency_sensitivity >= policy["urgent_sensitivity"]
            and signals.risk <= policy["fast_risk_max"]
            and mode != "orchestrated"
        )
    if fast_mode == "never" and not fast_override:
        return "default"
    if fast_mode == "inherit" and not (fast_override or standard_override):
        return None
    if wants_fast:
        if model.supports_fast:
            return "fast"
        if fast_override:
            raise ValueError(f"model {model.model_id} does not advertise Fast support")
    return "default"


def route_task(
    task: str,
    config: dict,
    registry: dict[str, ModelCapability],
    *,
    mode_override: str | None = None,
    model_override: str | None = None,
    effort_override: str | None = None,
    fast_override: bool = False,
    standard_override: bool = False,
) -> RoutingDecision:
    validate_config(config)
    config = effective_config(config)
    if len(task) > config["routing"]["max_task_chars"]:
        raise ValueError(f"task is longer than the configured limit ({config['routing']['max_task_chars']} characters)")
    if fast_override and standard_override:
        raise ValueError("--fast and --standard cannot be used together")
    if effort_override and not isinstance(effort_override, str):
        raise ValueError("effort must be a string")

    signals = classify_task(task, config)
    override = (mode_override or "auto").lower()
    if override in {"auto", "direct"}:
        mode = _mode_for(signals, config, allow_orchestration=override == "auto")
        if override == "direct" and mode == "orchestrated":
            mode = "sol"
    elif override in {"luna", "sol", "orchestrated"}:
        mode = override
    else:
        raise ValueError("mode must be auto, direct, luna, sol, or orchestrated")

    automatic = model_override is None
    role = "efficient" if mode == "luna" else "capable"
    coordinator = _select_model(role, config, registry, automatic=automatic, override=model_override)
    if mode == "orchestrated" and not coordinator.supports_multi_agent:
        if mode_override in {"orchestrated"}:
            raise ValueError(f"model {coordinator.model_id} is not marked multi-agent capable in the catalog")
        mode = "sol"
        coordinator = _select_model("capable", config, registry, automatic=automatic, override=model_override)

    if effort_override:
        if effort_override not in coordinator.reasoning_efforts:
            raise ValueError(f"model {coordinator.model_id} does not support effort {effort_override}")
        effort = effort_override
    else:
        effort = _effort_for(coordinator, _preferred_effort(mode, task, signals, config))

    worker_id: str | None = None
    worker_effort: str | None = None
    worker_count = 0
    if mode == "orchestrated":
        if config["routing"]["max_workers"] < 2:
            raise ValueError("orchestration requires routing.max_workers to be at least 2")
        worker = _select_model("worker", config, registry, automatic=True)
        worker_id = worker.model_id
        worker_effort = _effort_for(worker, config["efforts"]["worker"])
        worker_count = min(config["routing"]["max_workers"], max(2, signals.independent_tasks))

    service_tier = _tier_for(
        coordinator,
        signals,
        config,
        fast_override=fast_override,
        standard_override=standard_override,
        mode=mode,
    )
    reasons = list(signals.evidence)
    if mode == "luna":
        reasons.append("bounded work fits an efficient direct route")
    elif mode == "sol":
        reasons.append("reasoning, ambiguity, or risk favors a capable direct agent")
    else:
        reasons.append(f"{signals.independent_tasks} independent work areas justify a coordinator")
    if service_tier == "fast":
        reasons.append("latency preference requests Fast; it affects speed and allowance use")
    manual = bool(
        mode_override not in (None, "auto") or model_override or effort_override or fast_override or standard_override
    )
    return RoutingDecision(
        mode=mode,
        model_id=coordinator.model_id,
        effort=effort,
        service_tier=service_tier,
        reason="; ".join(reasons),
        signals=signals,
        multi_agent=mode == "orchestrated",
        multi_agent_version=coordinator.multi_agent_version if mode == "orchestrated" else None,
        worker_model_id=worker_id,
        worker_effort=worker_effort,
        max_workers=worker_count,
        confidence=signals.confidence,
        verification="read_only_review" if signals.read_only else "run_repository_tests_and_review_changes",
        manual_override=manual,
    )
