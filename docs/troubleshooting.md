# Troubleshooting

## `cx` cannot find Codex

Check `command -v codex` and `cx doctor`. If Codex uses a nonstandard executable path, prepend that directory to `PATH`. Do not copy authentication files to make `cx` work.

## No approved model is available

Run `cx models` and inspect lifecycle/account status. Use an explicit, read-only `cx evaluate --models <id> --confirm-usage`, then approve only a passing model with `cx models approve <id>`. A cached catalog entry alone is not account validation.

## Model discovery failed

Run `cx models refresh` after verifying Codex CLI works. Discovery reads the bundled catalog only. If it fails, the last-known registry is retained; a fresh installation without a registry fails closed.

## Model or effort was rejected

Run `cx models` to inspect advertised efforts. Use `cx --model <id> --effort <level> --dry-run "task"` before executing. Reasoning effort options vary by model.

## Fast was not applied

`cx` records the requested service tier, not necessarily the server's effective tier. Use `cx --fast` for a direct request; child agents have no independent Fast setting through this wrapper. The request may use more included allowance.

## Codex multi-agent did not delegate

`cx` enables the coordinator model's catalogued multi-agent version only for the orchestrated route and asks the coordinator to delegate when worthwhile. A task with tightly coupled edits may remain single-agent. `cx` cannot confirm child model or effort assignment from all CLI traces. Check `cx doctor` for the CLI feature status and `cx models` for the model's advertised version.

## The regular TUI did not route a prompt

Run `cx hooks status`, open a new session after installation, and use `/hooks` to review/trust the local command hook. The first notice says which route was requested. The queued prompt's second hook pass reports whether Codex's active model matches, differs, or was unavailable. `/status` also shows the active model. If `cx` reports a mismatch or says the model is unverified, that explicit `$cx` task is blocked. Correct the router or session before retrying; do not infer that the requested model ran. Share only the Codex CLI version and sanitized routing status; never share auth files or prompt text.

## A routed task exited successfully but seems incomplete

An exit code is process status, not proof of correct code. Inspect the diff, run the repository's verification, and record `failure` or verified `success` with `cx feedback`. The router does not automatically replay partial edits.

## Authentication

Sign into Codex with `codex login` using ChatGPT. `cx` strips API-key and custom-base-URL environment overrides so it cannot silently switch into API billing. Do not put tokens in the router's config file.
