---
name: cx
description: Explicitly route one Codex CLI task through the local adaptive router when the user invokes $cx; do not use for ordinary prompts.
---

`$cx <task>` is the user's explicit request to route this one task in the current Codex TUI conversation.

The installed `UserPromptSubmit` hook and `cx` CLI are the only routing implementation. Do not classify the task a second time, change `/model` yourself, or launch a separate `cx` session. Let the hook queue the task on the current thread and inspect its result:

- If the hook confirms the active model, continue with the task. The hook confirms the model slug only; the statusline is the visible check for model and reasoning effort.
- If the hook blocks the request, report its message and stop. Do not continue the task on the previously selected model.
- If no routing notice appears, do not assume the task was routed. Tell the user to check `cx hooks status` and restart Codex after installing the hook.

Never route ordinary prompts implicitly. The user must start the prompt with `$cx`.
