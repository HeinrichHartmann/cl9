# ADR 0011: Sealed Sessions with Profile-Local Init Scripts

**Date**: 2026-04-08

**Status**: Proposed

## Context

ADR 0009 established immutable profiles with explicit cloning for customization. This solved stale-profile-after-upgrade, but exposed a gap: **per-profile identity management**.

Users juggle multiple identities — a personal Anthropic account, a work key routed through AWS Bedrock, a client org's Vertex credentials — and need each cl9 profile to bind to its own credentials. With the current model, Claude Code reads auth from `~/.claude/` globally, so all sessions share one identity regardless of profile.

More broadly, sessions today depend on a two-stage config: cl9 supplies profile files (`CLAUDE.md`, `settings.json`, `mcp.json`) via CLI flags, but Claude Code still merges in user-level state from `~/.claude/`. This makes session context fuzzy — part cl9-managed, part home-directory state — and blocks per-session customization beyond what cl9 passes as flags.

### Requirements

- Sessions should be **sealed**: no reading from `~/.claude/` except for transient things like session history (tolerated).
- Identity should be **per-profile**, not global.
- cl9 should stay **identity-agnostic**: no code paths touching 1Password, keychain, AWS profiles, etc.
- Profiles should remain **immutable** (ADR 0009 holds).
- The customization hook should be **simple**: a shell script.

## Decision

### 1. Sealed Sessions via `claude --bare`

Claude Code's `--bare` flag skips loading hooks, plugin sync, keychain reads, `CLAUDE.md` auto-discovery, and settings from `~/.claude/`. Under `--bare`, Anthropic auth must come from `ANTHROPIC_API_KEY` or an `apiKeyHelper` in `--settings`; third-party providers (Bedrock, Vertex, Foundry) use their own environment-based credentials. All context — system prompt, MCP config, agent definitions — must be passed explicitly via flags.

cl9 launches **all** sessions with `--bare` plus explicit `--settings`, `--mcp-config`, `--append-system-prompt-file`, etc.

**Tolerated leak**: Claude still writes session history to `~/.claude/projects/<encoded-cwd>/*.jsonl` under `--bare`. ADR 0010's discovery flow depends on this. We tolerate the write — the invariant we care about is that no auth or config is *read* from `~/.claude/`.

### 2. Session-Local Runtime Directory

Each session gets a dedicated runtime directory:

```
.cl9/sessions/<session-id>/runtime/
  ├── CLAUDE.md
  ├── settings.json
  └── mcp.json
```

This is the **materialized session state**. It is built once at spawn time, derived from the profile plus whatever the init script writes, and passed to `claude` via explicit flags. Profiles stay immutable; the runtime dir is where per-session customization lands.

### 3. Profile-Local Init Scripts

Init scripts live at `~/.cl9/init/<profile-name>.sh`. When spawning a session with profile `P`, cl9 runs `~/.cl9/init/P.sh` if it exists. The script is responsible for populating `CL9_RUNTIME_DIR` — typically by copying from `CL9_PROFILE_DIR` and patching in credentials.

**Why a global init dir instead of a file inside the profile?** Built-in profiles are immutable package files (ADR 0009). Users can't add scripts inside them. A global `~/.cl9/init/` directory gives a stable, uniform customization point that works across built-in, user-global, and project-local profiles. The init script is *conceptually* profile-local — one script per profile name — but *physically* lives in user-writable state.

**Execution contract:**

- Script receives the **full environment that `claude` will inherit**. Whatever it exports propagates to the launched agent process. This is how credentials get injected — the script runs `op read op://Work/anthropic/api-key` (or equivalent) and exports `ANTHROPIC_API_KEY`.
- Script runs synchronously, before `claude` is spawned.
- Exit non-zero aborts the spawn with a clear error.
- Additional environment variables passed by cl9:

| Variable | Meaning |
|---|---|
| `CL9_PROFILE_DIR` | Absolute path to the immutable profile source |
| `CL9_RUNTIME_DIR` | Absolute path to the per-session runtime dir (writable) |
| `CL9_SESSION_ID`  | Session UUID |
| `CL9_SESSION_NAME`| Session name (if set) |

### 4. Default: No-Op Copy

If `~/.cl9/init/<profile-name>.sh` does not exist, cl9 falls back to a built-in no-op: copy all regular files from `CL9_PROFILE_DIR` to `CL9_RUNTIME_DIR`. This gives a "works out of the box" experience for users who don't need per-session customization and already have `ANTHROPIC_API_KEY` in their shell environment.

### 5. Shipped Stub

On first run, cl9 creates `~/.cl9/init/` and drops a `default.sh` stub containing commented-out examples — how to pull creds from 1Password, how to set Bedrock env vars, how to patch `settings.json` with `jq`. The user fills it in.

No inventory, no registry, no catalog — just a directory with shell scripts keyed by profile name. Users copy examples between projects manually.

### 6. Bake Once, Never Rebuild

Runtime directories are built once at spawn time. `cl9 agent continue` does **not** re-run the init script; it reuses the existing `runtime/` dir as-is.

This means short-lived tokens can expire. That is tolerated for now. If auth breaks on resume, the user forks to a new session or manually deletes the runtime dir. A future ADR can add an explicit refresh command if this pain justifies it.

### 7. Supersedes `profile clone` from ADR 0009

ADR 0009 proposed `cl9 profile clone <src> <dst>` as the primary way to customize a profile. With session-local runtimes and init scripts, cloning is unnecessary for the common cases — auth injection, settings tweaks, per-session MCP adjustments — because the init script writes to the runtime dir, not the profile.

`profile clone` and `profile diff` are **removed from scope**. Users who need persistent profile edits create a new profile directly under `~/.cl9/profiles/<name>/` (or the project-local equivalent). `profile list` and `profile audit` from ADR 0009 remain.

## Consequences

### Positive

- **Identity juggling works**: each profile has its own init script, its own credentials, its own scope.
- **cl9 stays identity-agnostic**: no code paths touching 1Password, AWS profiles, keychain, vault.
- **Clear ownership boundaries**: profile = immutable source, runtime = per-session mutable state, init.sh = user extension point.
- **Sealed sessions** eliminate config drift between what cl9 intends and what Claude actually loads.
- **Simpler than clone**: one extension point (a shell script) covers most customization needs.

### Negative

- **Token staleness on resume**: bake-once means expired credentials break resume; recovery is fork or manual delete.
- **User owns init.sh**: cl9 can't help when the script is broken; errors must be diagnosable and surfaced clearly.
- **Disk usage**: every session gets its own runtime dir. Small per-session overhead.
- **Coupling to `--bare`**: cl9 depends on Claude Code's `--bare` flag remaining stable and preserving the "explicit-only config" semantics.

### Breaking Changes

- Existing sessions spawned under the pre-0011 model have no runtime dir. On first resume, cl9 materializes one (running init if configured, else no-op copy). This is a one-time migration per session.
- `profile clone` / `profile diff` are dropped from the ADR 0009 plan.

## Implementation Plan

1. **Launch pipeline** (`cli.py`, `adapters.py`)
   - Create `.cl9/sessions/<id>/runtime/` at spawn time.
   - Resolve init script at `~/.cl9/init/<profile>.sh`; fall back to built-in no-op copy.
   - Run the init script synchronously with `CL9_*` env vars plus the full agent env; abort spawn on non-zero exit.
   - Launch `claude` with `--bare`, `--settings <runtime>/settings.json`, `--mcp-config <runtime>/mcp.json`, `--append-system-prompt-file <runtime>/CLAUDE.md`.

2. **Default stub**
   - On `cl9 init` (or first spawn if missing), create `~/.cl9/init/default.sh` as a stub with commented examples.

3. **Session metadata** (`sessions.py`)
   - Store `runtime_dir` path alongside `source_cwd`.
   - On resume, skip init — reuse existing runtime dir.
   - Migrate old sessions on first resume.

4. **ADR 0009 update**
   - Mark the `profile clone` and `profile diff` sections superseded by ADR 0011.
   - Keep `profile list`, `profile audit`.

5. **Tests**
   - No-op copy fallback produces a valid runtime dir.
   - Init script receives the documented env vars.
   - Non-zero exit aborts spawn with a clear error.
   - Resume does not re-run init.
   - Pre-0011 session resume triggers one-time materialization.

## Open Questions

- **MCP installation**: ADR 0009 had `cl9 profile install-mcp` which needed a mutable profile. Under this model, it either (a) writes to a user-owned profile directly, (b) is dropped, or (c) becomes an init.sh pattern. To be resolved in implementation.
- **Error surfacing**: when init.sh fails, what does the user see? Stderr passthrough is the minimum; structured error metadata is nicer but costs more.

## Relationship to Other ADRs

- **Builds on ADR 0009**: profiles remain immutable; session-local runtimes replace profile-local clones as the customization mechanism.
- **Supersedes parts of ADR 0009**: `profile clone` and `profile diff` are dropped.
- **Compatible with ADR 0010**: runtime dir path is new session metadata; does not affect resume/discovery.
- **Refines ADR 0008**: profile still binds to an agent executable, but the session runtime — not the profile — is what `claude` actually loads.

## References

- Claude Code `--bare` flag (`claude --help`)
- ADR 0008 — Profile-Bound Agent Executables
- ADR 0009 — Immutable Profiles with Explicit Cloning
- ADR 0010 — Session Discovery and Resume
- GitHub Issue #2 — Stale agent profiles after cl9 upgrade
