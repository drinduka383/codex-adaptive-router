from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

LIFECYCLE_STATES = {"discovered", "untested", "evaluated", "approved", "disabled", "deprecated"}


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    display_name: str
    reasoning_efforts: tuple[str, ...]
    service_tiers: tuple[str, ...] = ("default",)
    multi_agent_version: str | None = None
    lifecycle: str = "discovered"
    account_availability: str = "unknown"
    evaluation_passed: bool | None = None
    default_effort: str | None = None
    catalog_service_tiers: tuple[tuple[str, str], ...] = ()
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    last_verified_at: str | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id must not be empty")
        if self.lifecycle not in LIFECYCLE_STATES:
            raise ValueError(f"unknown model lifecycle state: {self.lifecycle}")

    @property
    def supports_fast(self) -> bool:
        return "fast" in self.service_tiers

    @property
    def supports_multi_agent(self) -> bool:
        return bool(self.multi_agent_version)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("reasoning_efforts", "service_tiers", "evidence"):
            value[key] = list(value[key])
        value["catalog_service_tiers"] = [list(row) for row in value["catalog_service_tiers"]]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ModelCapability:
        return cls(
            model_id=str(value["model_id"]),
            display_name=str(value.get("display_name") or value["model_id"]),
            reasoning_efforts=tuple(value.get("reasoning_efforts", ())),
            service_tiers=tuple(value.get("service_tiers", ("default",))),
            multi_agent_version=value.get("multi_agent_version"),
            lifecycle=value.get("lifecycle", "discovered"),
            account_availability=value.get("account_availability", "unknown"),
            evaluation_passed=value.get("evaluation_passed"),
            default_effort=value.get("default_effort"),
            catalog_service_tiers=tuple(tuple(row) for row in value.get("catalog_service_tiers", ())),
            first_seen_at=value.get("first_seen_at"),
            last_seen_at=value.get("last_seen_at"),
            last_verified_at=value.get("last_verified_at"),
            evidence=tuple(value.get("evidence", ())),
        )


@dataclass(frozen=True)
class TaskSignals:
    difficulty: float
    ambiguity: float
    scope: float
    risk: float
    verification_ease: float
    parallelism: float
    coordination_overhead: float
    latency_sensitivity: float
    expected_interactions: float
    confidence: float
    read_only: bool
    independent_tasks: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RoutingDecision:
    mode: str
    model_id: str
    effort: str
    service_tier: str | None
    reason: str
    signals: TaskSignals
    multi_agent: bool = False
    multi_agent_version: str | None = None
    worker_model_id: str | None = None
    worker_effort: str | None = None
    max_workers: int = 0
    confidence: float = 0.0
    verification: str = "user_or_repository_tests"
    manual_override: bool = False
