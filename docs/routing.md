# Routing policy

The policy is deterministic and local. Each signal is an estimate in `[0, 1]`, not a benchmark probability.

| Signal | What raises it |
|---|---|
| Difficulty | Debugging, architecture, risk, broad change requests |
| Ambiguity | Root-cause or open-ended investigation language |
| Scope | Multiple files, modules, services, or repository-wide requests |
| Risk | Security, authentication, concurrency, migrations, data integrity, production impact |
| Verification ease | Read-only inspection, tests, linting, compilation |
| Parallelism | Explicitly independent modules or workstreams |
| Coordination overhead | Sequential dependencies, same-file edits, shared mutable state |
| Interaction length | Debugging and broad tasks likely to need more tool use |
| Latency sensitivity | Urgent, interactive, or time-sensitive wording |

Initial policy order:

1. Uncertain or high-risk work uses the capable direct role.
2. Orchestration requires at least two independently described areas, enough scope, and low coordination overhead. Large scope alone does not trigger it.
3. Ambiguous, difficult, or interaction-heavy work uses the capable direct role.
4. Remaining bounded tasks use the efficient direct role.
5. Effort follows difficulty and risk. Automatic policy uses low/medium/high, and XHigh only for the strongest estimated need. Max/Ultra require an explicit override.
6. Fast is selected independently. Automatic Fast requires latency-sensitive wording, low risk, direct execution, and catalog support. Otherwise `default` overrides any inherited global Fast setting unless `fast_mode=inherit`.

The defaults are conservative starting thresholds, not empirically optimized. Tune through `cx config set` or edit the router's JSON file, then use `cx --dry-run` on representative prompts before using the new policy.

## Initial route matrix

The `efficient`, `capable`, and `worker` roles resolve to ordered model ID preferences in config. The IDs below are the initial preferences from the current user's Codex environment; the core policy does not depend on these names.

| Task category | Mode | Preferred role/model | Effort | Tier |
|---|---|---|---|---|
| Read-only inspection or docs | Luna Direct | efficient / `gpt-6-luna` | low | default; Fast only when latency-sensitive |
| Mechanical edit | Luna Direct | efficient / `gpt-6-luna` | high | default unless overridden |
| Routine bounded implementation | Luna Direct | efficient / `gpt-6-luna` | medium | default unless overridden |
| Ambiguous debugging | Sol Direct | capable / `gpt-6.1-sol` | medium/high | default unless overridden |
| Security/concurrency/data risk | Sol Direct | capable / `gpt-6.1-sol` | high; XHigh for strongest risk/difficulty | default unless overridden |
| Large independent investigations | Sol Orchestrated | capable root; worker / `gpt-6-luna` | medium root, low worker request | catalogued multi-agent version; child tier unavailable |

Manual `--model` and `--effort` are validated against the current catalog. A manual model may be untested, but remains unapproved in the registry.

## Fallback

Automatic routes use only lifecycle-approved models. If an efficient role is missing or unapproved, the configured fallback order and capable role are checked. If no eligible model exists, `cx` explains that an evaluation and manual approval are needed. It does not substitute a model that the registry has marked disabled or deprecated.

If orchestration is unavailable for the selected coordinator, automatic routing falls back to a capable direct route. A forced `--mode orchestrated` request fails clearly when the selected coordinator is not catalogued as multi-agent capable. V2 is explicitly enabled only when the coordinator's local catalog advertises V2; `cx doctor` reports whether the CLI feature is available and enabled by default.
