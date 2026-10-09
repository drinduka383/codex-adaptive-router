---
name: codex-adaptive-router
description: Inspect, explain, evaluate, and safely adjust the local cx Codex model router.
---

Use this skill when the user asks about Codex model routing, router configuration, model discovery, router diagnostics, evaluations, or updating routing preferences.

The `cx` CLI is the source of truth. Do not recreate its routing rules or infer model capabilities independently.

1. Start with `cx doctor` for runtime issues, `cx models` for discovered model state, `cx config` for preferences, or `cx explain "task"` for a route decision.
   Use `cx hooks status` to inspect explicit `$cx` routing in the regular TUI. If the user asks to enable it, use `cx hooks install`; then explain that they must open a new session and review/trust it with `/hooks`. Use `cx hooks uninstall` when asked to disable it. Ordinary prompts are never routed by this hook.
2. Explain catalog evidence and account verification separately. A model in the bundled catalog is not proof that the current account can use it.
3. New models remain untested until evaluated. Evaluation may consume the user's Codex allowance; run `cx evaluate` only after the user explicitly requests or confirms that usage.
4. A model can be approved only after an evaluation passes. Use `cx models approve <model-id>` only when the user requests the change.
5. To adjust settings, inspect `cx config`, then use `cx config set <key> <json-value>` when the user asks. Configuration history is maintained locally; `cx config rollback` restores the previous version.
6. Do not claim an interactive task succeeded from a zero exit code. Ask the user to verify the result or use the local repository's tests, then record it with `cx feedback <run-id> success --verified` or `failure`.
7. The router does not record prompts or Codex output. Do not copy credentials, API keys, private source, or auth files into router configuration or evaluation fixtures.
8. The orchestrated route requests a worker model and effort from native Codex subagents, but the CLI may not expose the effective child settings or child service tier. Report this as unverified unless a runtime trace confirms it.
9. In-session routing occurs only for a prompt beginning with `$cx`, using the installed CLI's `codex queue` command on the current thread. If the handoff reports a model mismatch or missing model, stop; the hook blocks that task. The hook confirms only the model slug. Effort and service tier are requested values; refer to the visible statusline or `/status` before describing the active reasoning setting.
