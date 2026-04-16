# ADR 0012: Full-Isolation Credential Lifecycle and Session GC

**Date**: 2026-04-16

**Status**: Accepted

## Context

ADR 0009 established project-local session management. ADR 0011 separated
environment from profile. This ADR covers a new isolation mode (`isolation:
"full"`) introduced for profiles that need complete config separation from the
user's global Claude Code installation, and the associated credential and
garbage-collection lifecycle.

### The isolation problem

Claude Code has two layers of config:

- **Compose** (default): cl9 passes `--settings` and `--strict-mcp-config` to
  layer profile config on top of Claude Code's normal discovery chain
  (`~/.claude/`). Auth, hooks, auto-memory, and LSP all come from the user's
  global config.

- **Full**: cl9 sets `CLAUDE_CONFIG_DIR=<runtime_dir>` so Claude Code sees
  *only* the runtime directory as its config root. No global settings, hooks,
  or auto-memory leak in. This is necessary for profiles that need hermetic
  isolation (e.g. profiles with conflicting MCP servers or different
  permissions).

### The credential problem

When `CLAUDE_CONFIG_DIR` is set, Claude Code computes a keychain service name
as:

```
Claude Code-credentials-<sha256(CLAUDE_CONFIG_DIR)[:8]>
```

This is different from the default entry (`Claude Code-credentials`). Without
intervention, a full-isolation session has no OAuth token and cannot
authenticate.

## Decision

### Credential copy at spawn time

When `isolation: "full"` is set in the profile manifest, cl9:

1. Computes the hashed keychain service name for the session's runtime
   directory.
2. Copies the full credential JSON blob (access token + refresh token) from
   `Claude Code-credentials` to `Claude Code-credentials-<hash>`.
3. Sets `CLAUDE_CONFIG_DIR=<runtime_dir>` in the subprocess environment.

Claude Code starts with a valid credential and manages its own refresh from
that point forward, writing refreshed tokens back to the hashed entry.

On the **next** spawn for the same session, cl9 overwrites the hashed entry
with a fresh copy from the source. This guarantees a valid starting point even
if the previous session's token was never refreshed.

This is macOS-only (`security` CLI). On other platforms the copy is a no-op;
auth falls back to whatever mechanism Claude Code uses there.

### The session DB is the keychain inventory

No separate inventory of keychain entries is maintained. The project-local
`state.db` is the authoritative record of which sessions exist. For each
session row, the hashed keychain entry (if any) can be derived deterministically
from the runtime directory path:

```
runtime_dir  = .cl9/sessions/<session_id>/runtime
keychain_key = Claude Code-credentials-<sha256(runtime_dir)[:8]>
```

This means:

- No registry to get out of sync
- Cleanup is tied to session deletion — `remove_runtime()` always calls
  `delete_keychain_credential()` before removing the runtime directory

### Session expiry and GC

Full-isolation sessions carry a keychain copy per session. Without cleanup,
copies accumulate. The chosen lifecycle:

- **Default prune threshold**: 7 days (down from 30 days). Runtime directories
  and keychain entries for idle sessions older than 7 days are removed by GC.
- **GC trigger**: `cl9 session prune` (or `--older-than Nd` for a custom
  threshold). Running prune records a timestamp (`cl9_meta.last_gc_at`) in
  `state.db`.
- **GC nudge**: On every cl9 invocation (except `project` and `man`), cl9
  checks whether the last GC was more than 7 days ago and whether any prunable
  sessions exist. If both are true, a reminder is printed to stderr:

  ```
  [cl9] gc has not run in a while. 3 stale session(s) waiting for removal.
  Run: cl9 session prune
  ```

  The nudge is a best-effort hint; it never blocks the user.

### Keychain entry count bound

With 7-day pruning and typical usage patterns, the keychain will hold at most a
few dozen entries at steady state. The entries are small JSON blobs. The upper
bound under normal conditions is `(active sessions) + (sessions created in the
last 7 days)`.

## Consequences

### Positive

- Full-isolation profiles can authenticate without an API key or manual setup
- Keychain entries are bounded and tied to session lifecycle
- Cleanup is automatic through the existing prune / delete path
- No separate credential inventory to maintain

### Negative

- On macOS, spawning a full-isolation session requires a Keychain access
  prompt the first time (or pre-authorization). Subsequent spawns reuse the
  same hashed entry.
- If the user's source credential (`Claude Code-credentials`) is absent (e.g.
  they use `ANTHROPIC_API_KEY` instead of OAuth), full-isolation sessions will
  fall through to API-key auth or fail. The copy is a no-op when the source
  is absent.

### Invariants

1. `remove_runtime()` always attempts keychain cleanup before filesystem cleanup.
2. `session delete` and `session prune` both call `remove_runtime()`, so
   neither leaves orphaned keychain entries.
3. The GC nudge fires at most once per 7-day window per project.
