# Architecture

`cx` is a local Python CLI wrapper around the installed Codex executable. It has no inference or classification client of its own.

```text
task text ──> local classifier ──> routing policy <── user JSON config
                                      │
                                      ├── model registry <── codex debug models --bundled
                                      ├── dry-run explanation
                                      └── argument-array invocation ──> codex CLI
                                                                           │
                                                          local outcome/usage metrics
```

## Components

- `classifier.py` maps wording into normalized, separate task signals.
- `policy.py` selects an execution mode, model role, effort, and requested service tier using configuration and discovered capabilities. Model identifiers live in user preferences, not the classifier or policy.
- `discovery.py` parses the bundled Codex model catalog and updates a local lifecycle registry. It never edits the Codex model cache.
- `execution.py` constructs argument arrays, removes API endpoint/key overrides, checks for ChatGPT CLI auth, launches the interactive session, and parses reported token usage from JSON events.
- `evaluation.py` runs explicit, bounded, read-only Codex evaluations. A passing evaluation marks a model evaluated; a separate manual approval step is required.
- `metrics.py` records local run summaries and verified user feedback without prompts or source content.
- `cli.py` composes the components and exposes run, explain, config, model, doctor, status, feedback, and evaluation commands.

The Codex plugin contains only a management skill. The CLI remains the sole routing implementation, so the skill cannot drift into a second classifier.

## Data and process boundaries

The wrapper launches one new Codex process per `cx <task>` command and inherits the current terminal for normal interactive use. It does not resume an existing Codex thread. Direct sessions explicitly disable native multi-agent; orchestrated sessions enable the catalogued multi-agent version and add coordinator instructions. V2 routes request narrow child context and worker model/effort overrides, but the wrapper cannot verify effective child settings or enforce shared-filesystem isolation beyond prompt guidance.

Only the task supplied to Codex contains source or user text. The router stores the route decision, category, duration, exit code, optional usage, and feedback. It does not write prompts or outputs to its config or metrics files.

## Failure behavior

- Missing or stale model discovery preserves the last-known registry. A fresh install without any registry fails closed until Codex supplies a catalog.
- Automatic routing selects only approved models. Explicit `--model` is a user override and may run a discovered but untested model, but does not approve it.
- A model selection/runtime error is reported; the router does not silently rerun the task under a different model.
- An interactive exit code is recorded but task correctness remains unknown until verified feedback is recorded.
- Evaluation is explicit, read-only, time-limited, and never changes route preferences.
