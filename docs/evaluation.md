# Evaluation

Model evaluation is opt-in because it invokes Codex and may consume subscription allowance. `cx evaluate` requires `--confirm-usage`. Defaults are two models, two cases, and 120 seconds per call, with hard caps of four models, five cases, and 300 seconds per call. It runs each prompt through `codex exec --ephemeral --sandbox read-only` and checks the final response against a configured regular expression. Direct mode disables subagents. Orchestrated mode requests one worker, no grandchildren, and the approved worker model/effort; the child count and effective settings remain unknown unless Codex exposes them. `--fast` requests the Fast tier for the coordinator independently of effort.

Example case file:

```json
[
  {
    "id": "read-project-name",
    "prompt": "Read pyproject.toml and return only the project name.",
    "expected_regex": "^codex-adaptive-router$"
  }
]
```

Run:

```sh
cx evaluate --models gpt-6-luna,gpt-6.1-sol --confirm-usage
cx evaluate --models gpt-6.1-sol --mode orchestrated --confirm-usage
cx evaluate --models gpt-6-luna --effort max --fast --confirm-usage
```

The shipped case only verifies that the CLI can return a controlled literal. It does not compare coding quality. A useful quality evaluation should use representative, read-only tasks with deterministic checks and should be reviewed before approving a model. Evaluation records duration, exit code, expected-pattern result, and token usage if Codex emitted it. It does not store prompts or responses.

Successful evaluation marks a model `evaluated`; `cx models approve <id>` is a separate human decision. Failure never auto-demotes an already approved working route. No single test changes defaults or thresholds.

## Interactive feedback

Interactive Codex sessions may not expose usage to `cx`, so usage stays unknown. A successful process exit also does not establish task success. After inspecting the repository and running its checks, record the outcome with `cx feedback <run-id> success --verified` or `cx feedback <run-id> failure`.

This records outcomes the user has verified. It does not yet compute a success-per-token score or auto-tune routing; there is not enough consistent usage and task-correctness data exposed by the CLI to support that safely.
