from codex_adaptive_router.models import ModelCapability


def model(
    model_id: str,
    *,
    lifecycle: str = "approved",
    multi_agent_version: str | None = "v2",
    efforts: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max"),
    tiers: tuple[str, ...] = ("default", "fast"),
) -> ModelCapability:
    return ModelCapability(
        model_id=model_id,
        display_name=model_id,
        reasoning_efforts=efforts,
        service_tiers=tiers,
        multi_agent_version=multi_agent_version,
        lifecycle=lifecycle,
        account_availability="observed_available" if lifecycle == "approved" else "unknown",
    )
