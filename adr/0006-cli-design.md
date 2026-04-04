# ADR 0006: CLI Design and Catalog Structure

**Date**: 2026-04-03

**Status**: Accepted

## Context

cl9 is a collaborative environment manager for humans and agents working on shared project contexts. As the tool evolves, we need a clear CLI structure that reflects its core purpose and avoids unnecessary complexity.

Early designs conflated several concerns:
- Shell environment management (Nix, direnv)
- Project scaffolding
- Agent configuration
- Session management

This ADR establishes a clean separation of concerns and a pragmatic CLI structure.

## Core Purpose

cl9 creates productive environments where humans and agents collaboratively work on a single context: the project directory.

Key principles:
1. **Project as shared context** - The directory is the collaboration surface
2. **Agent orchestration** - Multiple agents (architect, implementer, etc.) in one project
3. **Session continuity** - Resume conversations, preserve useful agent state
4. **Opinionated but flexible** - Ships with defaults, supports customization

## Decision

### Shell Environment is Orthogonal

Shell tooling (Nix flakes, direnv, .envrc) is **not** a core cl9 concern. It's one possible scaffolding opinion that some templates may include, but it's not part of the primary API.

Users interact with shell environments through their normal tools (direnv, nix develop, etc.), not through cl9. Templates may include shell-env files, but cl9 doesn't manage them after initialization.

To re-apply a template (e.g., after template updates), use `cl9 init --force`.

### `init` and `init --force`

`cl9 init` has two modes depending on whether the target is already initialized:

- **Fresh project**: `cl9 init` writes the selected template and creates project-local state.
- **Existing initialized project**: `cl9 init` becomes a preview command. It shows which files and directories would be added, changed, or clobbered, but does not write anything.
- **Existing initialized project with `--force`**: `cl9 init --force` immediately reapplies the selected template and may overwrite files. The user is responsible for moving files out of the way or accepting data loss.

This replaces the older idea of a separate public `update` command. Template reapplication is considered rare and intentionally explicit.

### Two Catalogs: Templates and Profiles

cl9 manages two distinct catalogs:

| Catalog | Purpose | When Used | CLI Flag |
|---------|---------|-----------|----------|
| **Templates** | Project scaffolding (directories, README, MEMORY) | `cl9 init` | `--type` / `-t` |
| **Profiles** | Agent configuration (CLAUDE.md, settings, MCPs) | `cl9 agent spawn` | `--profile` / `-p` |

These are intentionally separate because:
- One project may have multiple agent profiles active (architect + implementer)
- Different agents need different tools, MCPs, and instructions
- Templates are per-project; profiles are per-session

### Templates

Templates define project scaffolding applied at initialization:

```
src/cl9/templates/
├── default/
│   ├── README.md
│   ├── MEMORY.md
│   └── .gitignore
├── minimal/
└── python-ml/
    ├── README.md
    ├── MEMORY.md
    ├── notebooks/
    └── data/
```

User templates in `~/.config/cl9/templates/` override or extend built-ins.

Templates support variable substitution: `{{PROJECT_NAME}}`, `{{PROJECT_PATH}}`, `{{DATE}}`.

### Profiles

Profiles define agent configuration applied at spawn time:

```
src/cl9/profiles/
├── default/
│   └── CLAUDE.md
├── architect/
│   ├── CLAUDE.md
│   └── settings.json
└── implementer/
    ├── CLAUDE.md
    └── mcp.json
```

User profiles in `~/.config/cl9/profiles/` override or extend built-ins.

Profiles may include:
- `CLAUDE.md` - System prompt / instructions
- `settings.json` - Tool-specific settings
- `mcp.json` - MCP server configuration

The profile is recorded with the session and reused on `continue`.

### Future: Multi-Tool Support

The profile system is designed to support multiple agent tools:

| Tool | Profile Contents |
|------|------------------|
| Claude Code | CLAUDE.md, settings.json, mcp.json |
| Copilot | (future format) |
| Codex | (future format) |

The `tool` field in `agent_sessions` tracks which tool a session uses.

## CLI Structure

See also ADR 0007 for the `bin/` convention and project tools/dependencies model.

```
# Project management
cl9 project init [PATH] [-n NAME] [-t TYPE] [--force]
    Initialize a project with scaffolding from a template.
    Defaults: PATH=., TYPE=default
    If already initialized, show what would change.
    --force: Re-apply template to existing project, clobbering files.

cl9 project enter TARGET [-n|-p]
    Enter a project context (spawn subshell or tmux pane).
    - bin/ added to PATH
    - CL9_* env vars set

cl9 project run COMMAND [ARGS...]
    Run a command in the project environment.
    - CWD = project root
    - bin/ added to PATH
    - CL9_* env vars set

cl9 project list [-f FORMAT]
    List registered projects.

cl9 project register [PATH]
    Register a project in the global registry.

cl9 project remove NAME
    Unregister a project.

cl9 project prune
    Remove stale project registrations.

# Agent lifecycle
cl9 agent spawn [--name NAME] [--profile PROFILE]
    Create a new session with the specified profile.
    Default profile: "default" (or project's configured default)
    - bin/ added to PATH
    - CL9_* env vars set

cl9 agent continue [TARGET]
    Resume an existing session.
    TARGET: session name, ID prefix, or "latest" (default)

cl9 agent fork TARGET [--name NAME] [--profile PROFILE]
    Fork a session. Optionally change the profile.

# Session management
cl9 session list
    List sessions in the current project.

cl9 session prune [--older-than DURATION]
    Remove old idle sessions.
    Default: 30 days

cl9 session delete TARGET [--force]
    Delete a specific session.

# Utility
cl9 completion SHELL
    Output shell completion script.

cl9 man
    Display manual page.

# Top-level aliases (convenience)
cl9 init  → cl9 project init
cl9 enter → cl9 project enter
cl9 run   → cl9 project run
```

## Catalog Resolution

### Templates

Resolution order for `--type <name>`:
1. User templates: `~/.config/cl9/templates/<name>/`
2. Built-in templates: `<cl9-package>/templates/<name>/`
3. Local path: `<name>/` if it exists as a directory

### Profiles

Resolution order for `--profile <name>`:
1. Project-local profiles: `.cl9/profiles/<name>/`
2. User profiles: `~/.config/cl9/profiles/<name>/`
3. Built-in profiles: `<cl9-package>/profiles/<name>/`

Project-local profiles allow per-project customization without polluting global config.

## Project Configuration

Projects can set defaults in `.cl9/config.json`:

```json
{
  "name": "my-project",
  "version": "1",
  "default_profile": "careful-coder"
}
```

When `cl9 agent spawn` is called without `--profile`, it uses:
1. Project's `default_profile` if set
2. Otherwise `"default"`

### Profile Layout and Compatibility

New projects use:

```text
.cl9/profiles/default/
```

There is no compatibility goal for older `.cl9/claude/` layouts. Existing projects are expected to adopt the new structure by re-running:

```bash
cl9 init --force
```

This ADR intentionally chooses a clean profile model over backward compatibility with earlier experimental layouts.

## Session Schema

Sessions record their profile for continuity:

```sql
CREATE TABLE agent_sessions (
    session_id TEXT PRIMARY KEY,
    name TEXT,
    profile TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    source_cwd TEXT NOT NULL,
    forked_from_session_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

Target resolution for `cl9 agent continue` is intentionally pragmatic. Initial implementations may support only:

- `latest`
- exact session ID
- exact session name

Prefix matching and richer disambiguation may be added later once real usage patterns are clearer.

## Session Portability

Session history is currently stored in Claude's global config (`~/.claude/`), tied to project path. Moving a project may orphan sessions.

`cl9` owns the session semantics it exposes, even if Claude's native persistence model is imperfect. The tool should track and manage the sessions it spawns as project-local state.

For the next version, however, `cl9` does **not** mutate or clean up Claude-owned history in `~/.claude/` or Claude Desktop state. Commands such as `cl9 session prune` and `cl9 session delete` operate on `cl9`'s own project-local metadata only.

Later versions may add safe cleanup or synchronization with Claude's global state if we determine a robust way to do so.

## Consequences

### Positive

- Clear separation: templates (project) vs profiles (agent)
- Multiple agent flavors per project supported naturally
- Shell/Nix is just template content, not a special concern
- `--force` for rare template re-application, no incremental update complexity
- Extensible to future tools (Copilot, Codex)
- Profile recorded with session enables consistent `continue`

### Negative

- Two catalogs to understand (templates + profiles)
- Profile resolution has multiple layers (project → user → built-in)
- Session portability remains a limitation
- `init --force` is intentionally destructive

### Trade-offs Accepted

- Simplicity over incremental template updates (use `--force` to clobber)
- Per-session profiles over dynamic profile switching mid-session
- Accept session portability limitation for now
- Clean break over compatibility with earlier experimental project layouts
