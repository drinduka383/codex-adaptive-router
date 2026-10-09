# Security and privacy

- Routing classification is local and deterministic; no model or external classifier receives task text.
- Codex subprocesses inherit the existing ChatGPT login. `cx` removes `OPENAI_API_KEY` and `OPENAI_BASE_URL` before launching Codex and refuses to run if `codex login status` does not report ChatGPT authentication.
- Router config, registry, config history, and metrics use user-only file permissions. They contain model IDs and routing outcomes, not credentials.
- Prompts, source files, and model output are not written to router logs. Evaluation response files are temporary and removed after each case.
- Evaluation requires `--confirm-usage`, uses ephemeral sessions and a read-only sandbox, caps models/cases, and has a timeout.
- Subprocesses use an argument array. User task text is one argument, never shell source.
- The optional `cx hooks install` command backs up `hooks.json` before adding the router hook and preserves other entries. It migrates only the router's marked legacy block from `config.toml`; uninstall removes only the router hook and any such legacy block. It does not change Codex model defaults, custom agents, skills, plugins, or marketplaces.
- The prompt hook uses the current ChatGPT login, strips API-key/base-URL environment overrides, stores no prompt text, and uses a temporary prompt hash plus selected model/effort/tier for one-time handoff confirmation. Unmarked prompts pass through without routing; if authentication, discovery, queueing, or model confirmation fails for an explicit `$cx` request, the hook blocks that task instead of silently running it on the current model.
- Orchestrated children share the working tree. The coordinator is instructed to use read-only tasks or disjoint file ownership, but that is not a filesystem-enforced boundary.
- GitHub Actions runs only tests and packaging. It requires no Codex credentials and does not perform model evaluations.
