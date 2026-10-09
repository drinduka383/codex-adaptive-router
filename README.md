# Codex Adaptive Router

`cx` chooses a Codex CLI model and execution mode before opening a fresh Codex session. It uses local task heuristics and the model capabilities exposed by the installed Codex CLI. It does not send task text to a classification service or require an API key.

The goal is to complete more verified work per unit of Codex allowance while controlling latency and coordination overhead. The included thresholds are starting hypotheses; they are not measured guarantees.

## Quick start

Requirements: Python 3.11 or newer and a ChatGPT-authenticated Codex CLI. The current development environment is Codex CLI 0.162.0 on Fedora Linux.

From a checkout:

```sh
python3 -m pip install --user .
cx config init
cx doctor
cx models
```

If your Python installation does not expose user scripts on `PATH`, install with `pipx install .` or add the resulting script directory to `PATH`. To use a checkout without packaging, link `bin/cx` into a directory already on `PATH`:

```sh
ln -s "$(pwd)/bin/cx" "$HOME/.local/bin/cx"
```

Try a route without starting Codex:

```sh
cx --dry-run "implement pagination for this API"
cx explain "fix the authentication race condition"
```

New catalog models are not used automatically. The first evaluation is explicit because it consumes Codex allowance:

```sh
cx evaluate --models gpt-6-luna,gpt-6.1-sol --confirm-usage
cx evaluate --models gpt-6.1-sol --mode orchestrated --confirm-usage
cx evaluate --models gpt-6-luna --effort max --fast --confirm-usage
cx models approve gpt-6-luna
cx models approve gpt-6.1-sol
```

The default direct evaluation is a small read-only response check, not a coding-quality benchmark. Orchestrated evaluation uses a deliberate one-worker smoke case. Both are opt-in. Defaults are two models, two cases, and 120 seconds per call; hard caps are four models, five cases, and 300 seconds per call. Use repository-specific cases to evaluate work you can verify.

## Daily use

```sh
cx "implement pagination for this API"
cx --mode luna "rename this function and update its callers"
cx --mode sol "investigate why the request handler deadlocks"
cx --mode orchestrated "inspect four independent packages and summarize their boundaries"
cx --model <model-id> --effort <supported-effort> "task"
cx --fast "time-sensitive bounded task"
cx --standard "use the standard service tier for this task"
```

Each command starts a **new interactive Codex session** in the current working directory. It does not attach to or continue another Codex conversation. Luna and Sol are default model preferences in the JSON config, not identifiers embedded in the routing algorithm.

`--mode direct` means choose a single agent from the task signals and never enable subagents. `--mode luna` and `--mode sol` force the corresponding direct role. `--mode orchestrated` enables the multi-agent feature version advertised by the coordinator model; the coordinator still decides whether actual delegation is useful.

## Management commands

```sh
cx models                  # capabilities, lifecycle, and account evidence
cx models refresh          # read the current Codex bundled catalog
cx models approve <id>     # approve only after a passing evaluation
cx models disable <id>
cx models enable <id>      # returns it to untested; evaluate again
cx config                  # inspect effective preferences
cx config set routing.max_workers 3
cx config set service_tier.fast_mode '"never"'
cx config rollback
cx doctor
cx status
cx feedback <run-id> success --verified
cx evaluate --models <id>[,<id>] --cases <file> --confirm-usage
```

`cx --dry-run` and `cx explain` are local and do not invoke a model. `cx evaluate` is opt-in, bounded by the configured model/case limits, ephemeral, and read-only. It never auto-approves a model or edits routing preferences.

## Routing and model lifecycle

The task classifier scores difficulty, ambiguity, repository scope, risk, verification ease, parallelism, coordination overhead, interaction length, and latency sensitivity independently. A simple deterministic policy maps those signals to a model role and effort. If signals are weak, it falls back to a capable direct route. No extra model call is made to classify a task.

The engine reads `codex debug models --bundled`, an installed Codex catalog interface, and stores its own snapshot under the router state directory. The `--bundled` flag avoids refreshing or writing Codex's internal model cache. New models enter `discovered`, then `untested` on a later observation. A passing opt-in evaluation sets `evaluated`; `cx models approve` is a separate manual step. Missing models become `deprecated`; disabled models remain disabled.

The bundled catalog reports model IDs, supported reasoning efforts, advertised service tiers, and multi-agent compatibility. It does not prove account entitlement. Only a successful Codex evaluation records observed account availability. If discovery fails, `cx` keeps using its last-known registry; it never replaces an approved preference with a newly discovered model.

The default policy uses the efficient role for routine bounded work, the capable role for ambiguity/risk, and orchestration only when independent work areas and low coordination overhead are detected. The included `balanced`, `economical`, `fast`, and `quality` presets adjust routing preferences. Use `cx config set preset '"economical"'` to change presets. Edit the human-readable file for custom model IDs and thresholds.

## Fast mode and orchestration

Fast is separate from reasoning effort. The `auto` preset requests Fast only for a latency-sensitive, bounded, lower-risk task when the selected model advertises Fast. Other routes explicitly request `default`, which overrides a Fast setting inherited from the user's Codex config. `--fast`, `--standard`, and the `fast_mode` setting can override that choice.

The local Codex catalog maps Fast to a `priority` capability displayed as Fast; the current CLI accepts `service_tier=fast`. Runtime responses do not expose a verifiable effective service tier in all modes, so metrics distinguish the requested tier from the actual tier (unknown unless reported).

Orchestrated sessions enable the coordinator model's advertised Codex multi-agent version and give it a worker model/effort preference, a maximum worker count, and file ownership guidance. V2 routes request narrow child context (`fork_turns=none`) and per-child model/effort overrides where the installed CLI exposes them. `cx` cannot verify the effective child assignment or child service tier from all CLI traces. The prompt limits workers to the configured total and forbids recursive delegation. Orchestration is best-effort; the shared filesystem means independent workers should default to read-only investigations or disjoint file ownership.

## Configuration, metrics, and privacy

Router config: `${XDG_CONFIG_HOME:-~/.config}/codex-adaptive-router/config.json`.

Registry, config history, and local metrics: `${XDG_STATE_HOME:-~/.local/state}/codex-adaptive-router/`.

Config and logs are created with user-only permissions. Metrics include the selected mode/model/effort, requested tier, duration, exit status, reported usage if available, observed worker information if available, and optional verified user feedback. Prompts and model outputs are not stored. Interactive Codex usage usually exposes no token count to this wrapper; that field stays empty. Subscription use has no fabricated dollar estimate.

`cx` removes `OPENAI_API_KEY` and `OPENAI_BASE_URL` from Codex subprocess environments, then requires `codex login status` to report ChatGPT authentication. It does not read, copy, or export Codex credentials. The router does not contact model-selection services or GitHub at runtime.

An exit code of zero is not treated as a verified task success. After reviewing the repository result and its tests, record feedback using `cx feedback`. No automatic retry or cross-run escalation is performed; the next route must be chosen explicitly so partial edits are not replayed blindly.

## Plugin

The repository root contains a skills-only plugin manifest and a `codex-adaptive-router` management skill. Codex CLI supports local plugin marketplaces; to test this plugin from a checkout:

```sh
codex plugin marketplace add /path/to/codex-adaptive-router
codex plugin marketplace list
codex plugin add codex-adaptive-router@codex-adaptive-router-local
```

The plugin skill delegates decisions to `cx`. It is packaged here but is not enabled in the user's Codex config by the project installer.

## Development

```sh
python3 -m unittest discover -v
python3 -m compileall -q src
ruff check src tests
ruff format --check src tests
```

CI checks tests, style, bytecode compilation, entry-point installation, and wheel creation. It does not need Codex authentication and never runs paid evaluations.

See [architecture](docs/architecture.md), [routing](docs/routing.md), [configuration](docs/configuration.md), [evaluation](docs/evaluation.md), [installation](docs/installation.md), [security](docs/security.md), and [troubleshooting](docs/troubleshooting.md).

## Compatibility and limitations

- Designed for Codex CLI 0.162.0 and later, Python 3.11+, Linux/macOS shells, and ChatGPT CLI authentication. Other CLI versions must pass `cx doctor` and the bundled model-catalog probe.
- The installed catalog is a capability snapshot, not an account entitlement API.
- Native child model/effort assignments, actual child count, and child Fast are not confirmed by this wrapper.
- Automatic verification and escalation across interactive sessions are intentionally absent.
- Heuristic thresholds are configurable but have not been calibrated on a task-success dataset.
- Accurate subscription quota per successfully completed task requires Codex to expose usage for interactive sessions and verified outcomes to be recorded.

## License

MIT. See [LICENSE](LICENSE).
