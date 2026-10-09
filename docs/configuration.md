# Configuration

`cx config init` creates `${XDG_CONFIG_HOME:-~/.config}/codex-adaptive-router/config.json` with mode `0600`. Until then, `cx` uses the package defaults in memory.

The important sections are:

- `preset`: `balanced`, `economical`, `fast`, or `quality`.
- `models.roles`: ordered model ID preferences for `efficient`, `capable`, and `worker`.
- `models.fallback_order`: approved model candidates used when a preferred ID is absent.
- `routing`: confidence, difficulty, risk, scope, parallelism and coordination thresholds; maximum worker guidance and input length.
- `efforts`: preferred reasoning levels by work type. The model catalog filters unsupported values.
- `service_tier.fast_mode`: `auto`, `always`, `never`, or `inherit`.
- `discovery`: refresh period, stale-warning age, and maximum bundled-catalog size.
- `evaluation`: maximum case/model count and per-call timeout.

Prompt/output logging is not configurable; the router does not persist either.

Examples:

```sh
cx config set preset '"economical"'
cx config set models.roles.efficient '["my-approved-model"]'
cx config set routing.max_workers 3
cx config set service_tier.fast_mode '"inherit"'
cx config rollback
cx config reset
```

Config edits create a timestamped prior version in the private state directory. `cx config rollback` restores the previous version. No router config value changes `~/.codex/config.toml`.

Automatic cross-run retries/escalations are not implemented. The two related config values remain zero until a supervisor can distinguish failed verification from harmless tool exits.
