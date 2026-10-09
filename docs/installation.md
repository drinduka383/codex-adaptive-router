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

## Per-turn routing in the Codex TUI

`cx` can route prompts in a regular `codex` session through Codex's `UserPromptSubmit` hook and the installed CLI's `codex queue` command. This keeps the current conversation and its scrollback; it does not start a separate `cx` TUI.

Run `cx models` before enabling the hook so the local model capability registry is ready. The hook uses that cached registry and does not refresh models on every turn.

```sh
cx hooks status
cx hooks install
```

Installation backs up `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) before adding one marked hook block. It validates TOML first and preserves all other settings and hooks. Start a new Codex session and use `/hooks` to review and trust the local command. The hook displays the selected model, effort, and tier through Codex's visible `systemMessage`; check the TUI model indicator too, because queue-based model changes depend on the installed runtime. See the [official Hooks documentation](https://learn.chatgpt.com/docs/hooks) for hook trust and event behavior.

To disable automatic routing, run `cx hooks uninstall`; this removes only the marked block and makes another timestamped backup. Restart active Codex sessions after install or removal. A routing or queue failure is fail-open: Codex continues the original prompt on its current model.

The plugin itself is still only a management skill. Codex CLI 0.162.0 lists plugin hooks as removed, so the hook must be installed through the user's config rather than claimed as plugin-installed behavior. The current project has not yet confirmed that `codex queue --model` updates the TUI's footer indicator; see the compatibility note in the README.

## Plugin

The root `plugin.json` packages the management skill. From the checkout root, load and install it with:

```sh
codex plugin marketplace add ./.agents/plugins
codex plugin add codex-adaptive-router --marketplace codex-adaptive-router-local
```

This changes Codex plugin configuration and is optional; `cx` itself does not require plugin activation. Remove it with:

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
