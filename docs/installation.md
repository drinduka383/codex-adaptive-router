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

## Plugin

The root `plugin.json` packages the management skill. To load it from a local checkout, use the local marketplace commands in the README. This changes Codex plugin configuration and is optional; `cx` itself does not require plugin activation.

## Update

For a checkout install, run `git pull --ff-only` in the project and reinstall with `python3 -m pip install --user .`, or re-link `bin/cx` if using the symlink workflow. For a pipx install, run `pipx upgrade codex-adaptive-router` after publishing a release.

The `cx update` command prints the appropriate source-checkout guidance; it does not fetch or execute remote code.

## Uninstall / rollback

Remove the `cx` symlink from the script directory, or use `python3 -m pip uninstall codex-adaptive-router` / `pipx uninstall codex-adaptive-router`. Remove the optional plugin through `codex plugin remove` and its marketplace through `codex plugin marketplace remove`.

To remove router settings and history, delete only the `codex-adaptive-router` subdirectories under the configured XDG config/state roots. This does not touch Codex authentication or `~/.codex/config.toml`. Keep those directories if you may want to restore configuration or review local metrics.
