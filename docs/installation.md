# Installation and updates

## Install from GitHub

The standalone CLI is not currently published to PyPI. Install it directly from this public repository with `pipx`:

```sh
pipx install git+https://github.com/drinduka383/codex-adaptive-router.git
cx config init
cx doctor
```

`pipx` keeps the router in an isolated Python environment. It does not replace `codex`, change Codex authentication, or install the optional plugin. If `pipx` is missing, install it through your operating system's package manager.

## Install from a local checkout

```sh
pipx install .
cx config init
cx doctor
```

The command is separate from `codex` and never replaces it.

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

The root `plugin.json` packages both skills. Add the public repository marketplace and install the plugin with:

```sh
codex plugin marketplace add drinduka383/codex-adaptive-router
codex plugin marketplace list
codex plugin add codex-adaptive-router --marketplace codex-adaptive-router-local
```

Plugin installation makes `$cx` and router management skills discoverable in the TUI; the standalone `cx` CLI performs routing. Install the CLI separately. Remove the plugin with:

```sh
codex plugin remove codex-adaptive-router --marketplace codex-adaptive-router-local
codex plugin marketplace remove codex-adaptive-router-local
```

Plugin installation does not install the optional TUI hook. To route an explicit `$cx` task in the current TUI session, follow the separate [hook setup](#explicit-per-prompt-routing-in-the-codex-tui) steps above.

## Update

For a checkout install, run `git pull --ff-only` in the project and reinstall with `pipx install --force .`, or re-link `bin/cx` if using the symlink workflow. For a GitHub `pipx` install, run `pipx upgrade codex-adaptive-router`.

The `cx update` command prints the appropriate source-checkout guidance; it does not fetch or execute remote code.

## Uninstall / rollback

Remove the `cx` symlink from the script directory or run `pipx uninstall codex-adaptive-router`. Remove the optional plugin through `codex plugin remove` and its marketplace through `codex plugin marketplace remove`.

To remove router settings and history, delete only the `codex-adaptive-router` subdirectories under the configured XDG config/state roots. This does not touch Codex authentication or `~/.codex/config.toml`. Keep those directories if you may want to restore configuration or review local metrics.
