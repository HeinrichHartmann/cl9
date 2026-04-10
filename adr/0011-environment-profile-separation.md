# ADR 0011: Environment–Profile Separation and direnv Integration

**Date**: 2026-04-09

**Status**: Proposed

## Context

ADR 0009 defined profiles as read-only agent configuration templates and sessions as per-spawn runtime snapshots. In practice, two concerns have become entangled:

1. **The project environment** — the shared human+LLM workspace: shell PATH, nix packages, tools like `python3`, `ztoken`, `gh`. Defined by `.envrc` / `flake.nix`. Owned by the project.

2. **The agent profile** — the agent's identity: which model, which auth, which MCP servers, which statusline, which system prompt. Owned by the profile.

These are independent axes. A single project may spawn agents with different profiles (Zalando Claude, Zalando Codex, personal default). A single profile may be used across many projects. But the current code conflates them in several places:

- **`agent_command` in the profile manifest** bakes `direnv exec $CL9_PROJECT_ROOT` into the profile. This makes the profile depend on a specific environment manager. A profile that says `"agent_command": "direnv exec $CL9_PROJECT_ROOT claude"` cannot be used on a machine without direnv, or in a project that uses `mise` or `devbox` instead.

- **`--bare` was removed because it stripped too much**, but the underlying problem was that profiles shipped both agent config (settings, MCP, statusline) and environment expectations (python3 on PATH, ztoken available) without distinguishing the two.

- **The statusline depends on `python3`**, which is a project-environment concern (available via nix), not a profile concern. The profile should use cl9's own Python — the same interpreter that runs cl9 itself.

- **ADR 0009 specified `--bare` for sealed sessions**, but `--bare` also strips statusline, hooks, and most tools, making it unsuitable for interactive use. The ADR needs a revised launch strategy.

## Decision

### Clean separation

| Concern | Owner | Mechanism |
|---|---|---|
| Shell tools, PATH, credentials | **Project environment** | `.envrc` + `flake.nix` (or any env manager) |
| Agent config: model, auth, MCP, statusline, system prompt | **Profile** | `manifest.json`, `settings.json`, `mcp.json`, `CLAUDE.md` / `INSTRUCTIONS.md` |
| Per-project agent overrides | **Project init script** | `.cl9/init/init.py` |
| Which profile to use by default | **Project config** | `.cl9/config.json` → `default_profile` |

Profiles must not assume a specific environment manager. Projects must not dictate agent configuration.

### direnv is the user's responsibility

cl9 does not wrap agent launches with `direnv exec` or auto-detect `.envrc`. The user is expected to run `cl9 spawn` from within an activated direnv environment (either via `direnv exec .` or direnv's auto-hook on `cd`). cl9 inherits `os.environ` and passes it through to the agent subprocess. The agent and all its children (statusline, MCP servers) inherit the same environment.

This is simpler than auto-detection, avoids re-evaluating nix flakes on every spawn, and matches how every other CLI tool works — you activate your env, then run your commands in it.

### `agent_command` removed from profiles

The `agent_command` manifest key is removed. Profiles declare `tool` and `executable` (as before ADR 0008). The launch wrapper is cl9's responsibility, not the profile's. If a future environment manager needs support, it gets a cl9-level integration (like direnv), not a per-profile hack. There is no project-level override — environment management is cl9's job, not a per-project configuration concern.

### Profile statusline is a self-contained uv script

The statusline script is a profile asset. It must not depend on the project's nix shell providing `python3`. Instead, the script uses a PEP 723 shebang and inline metadata:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
import json, sys
# ... statusline logic ...
```

The script is marked executable. The settings.json command simply executes it:

```json
{
  "statusLine": {
    "type": "command",
    "command": "${CL9_RUNTIME_DIR}/statusline.py"
  }
}
```

The kernel resolves the shebang, finds `uv` on PATH, and uv handles Python interpreter resolution and any declared dependencies. No `python3` or `uv run` in the command string — the script is self-contained, same as any `#!/usr/bin/env bash` script.

### Revised launch strategy (replacing `--bare`)

ADR 0009 specified `claude --bare` for sealed sessions. In practice, `--bare` is too restrictive for interactive use (strips statusline, MCP servers, hooks, most tools). The revised strategy for interactive spawns:

- Launch with `claude` (no `--bare`), plus `--settings`, `--mcp-config`, and `--append-system-prompt-file` pointing at the runtime directory.
- `--settings` has higher precedence than user/project settings in Claude Code's merge chain, so profile settings override `~/.claude/settings.json`.
- Claude Code's native CLAUDE.md discovery is left active. The project's own `CLAUDE.md` (if any) composes with the profile's system prompt.
- MCP servers from `--mcp-config` are additive.

For non-interactive or advanced use, the user can pass flags through: `cl9 spawn -- --bare -p "query"`. cl9 does not need to handle these cases specially.

This gives profiles full control over agent config while allowing Claude Code's native features (statusline, hooks, project CLAUDE.md) to work. This decision supersedes the `--bare` requirement in ADR 0009.

## Consequences

### Good

- **Profiles are portable.** A Zalando profile works in any project, on any machine, regardless of environment manager. No `direnv exec` baked in.
- **No magic.** cl9 does not auto-detect or wrap with direnv. The user activates their env, then runs cl9. Same as every other CLI tool.
- **Statusline always works.** `uv run --script` resolves its own Python. No dependency on the nix shell or cl9's install path.
- **Simpler profile manifests.** Just `tool` and `executable` (or just `tool` if executable matches). No `agent_command` to confuse authors.

### Neutral

- **`--bare` is gone for interactive sessions.** Claude Code's native discovery runs. Profile settings override via `--settings` precedence. This is a pragmatic trade: we lose hermeticity but gain a working statusline, hooks, and tool set. Users can still pass `--bare` via `cl9 spawn -- --bare`.

### Bad

- **Non-direnv env managers need custom integration.** A project using `mise` or `devbox` instead of direnv would need a cl9-level integration for that manager. This is acceptable: direnv covers >90% of the user base, and new integrations can be added as first-class cl9 features when demand arises.
- **Profile assets own their own dependencies.** A profile that ships a `uv run --script` statusline requires `uv` on PATH; a profile with a bash statusline does not. cl9 does not validate or manage these dependencies — the profile author owns them.
