# Installation and updates

## Local checkout

```sh
python3 -m pip install --user .
cx config init
cx doctor
```

If the Python user script directory is not on `PATH`, use `pipx install .` or symlink `bin/cx` into a directory on `PATH`. The command is separate from `codex` and never replaces it.

For local source development without packaging:

```sh
ln -s "$(pwd)/bin/cx" "$HOME/.local/bin/cx"
```

The project needs Python 3.11+ and a Codex CLI whose `codex debug models --bundled` output is understood by `cx`. Run `cx doctor` after Codex upgrades.

## Explicit per-prompt routing in the Codex TUI

The `UserPromptSubmit` hook adds `$cx <task>` as an explicit, one-turn route in a regular `codex` session. It queues the task on the same conversation and keeps the existing scrollback; it does not start a separate TUI.

Run `cx models` before enabling the hook so the local model capability registry is ready. The hook uses that cached registry and does not refresh models on every turn.

```sh
cx hooks status
cx hooks install
```

Installation backs up `~/.codex/hooks.json` (or `$CODEX_HOME/hooks.json`) before adding the router hook, preserving other entries. If it finds the router's older marked hook block in `config.toml`, it backs up that file and moves the entry into `hooks.json`, keeping one hook representation in the user configuration layer. Start a new session and use `/hooks` to review and trust the local command. Run `cx hooks status` to check installation.

Type `$cx inspect the four authentication modules` to route one task. Ordinary prompts do not get classified or rerouted. The hook removes `$cx`, selects a route locally, updates the active conversation through the running app-server's experimental WebSocket `thread/settings/update` method, then queues the task on that same thread. If the update, route selection, or queueing fails, or the next hook pass cannot confirm the requested model, Codex blocks that explicit task instead of running it on the previously selected model. The hook event exposes the active model slug, but not the effective reasoning effort or service tier; use the statusline or `/status` to inspect model and reasoning effort. See the [official Hooks documentation](https://learn.chatgpt.com/docs/hooks) for hook payload and event behavior.

To disable in-session routing, run `cx hooks uninstall`; this removes only the router hook from `hooks.json` and any older marked block in `config.toml`, backing up each changed file. Restart active Codex sessions after install or removal.

The plugin packages a management skill and an explicit `$cx` invocation skill. Codex CLI 0.162.0 does not support plugin hooks, so the actual routing hook remains installed separately in the user's `hooks.json`. The route confirmation uses the next `UserPromptSubmit` event; the hook payload confirms the active model slug only, while effort and tier remain unverified by the hook.

## Plugin

The root `plugin.json` packages both skills. From the checkout root, load and install the plugin with:

```sh
codex plugin marketplace add .
codex plugin add codex-adaptive-router --marketplace codex-adaptive-router-local
```

Plugin installation makes `$cx` discoverable from the TUI; the router CLI and separately installed hook perform the routing. Remove the plugin with:

```sh
codex plugin remove codex-adaptive-router --marketplace codex-adaptive-router-local
codex plugin marketplace remove codex-adaptive-router-local
```

## Update

For a checkout install, run `git pull --ff-only` in the project and reinstall with `python3 -m pip install --user .`, or re-link `bin/cx` if using the symlink workflow. For a pipx install, run `pipx upgrade codex-adaptive-router` after publishing a release.

The `cx update` command prints the appropriate source-checkout guidance; it does not fetch or execute remote code.

## Uninstall / rollback

Remove the `cx` symlink from the script directory, or use `python3 -m pip uninstall codex-adaptive-router` / `pipx uninstall codex-adaptive-router`. Remove the optional plugin through `codex plugin remove` and its marketplace through `codex plugin marketplace remove`.

To remove router settings and history, delete only the `codex-adaptive-router` subdirectories under the configured XDG config/state roots. This does not touch Codex authentication or `~/.codex/config.toml`. Keep those directories if you may want to restore configuration or review local metrics.
