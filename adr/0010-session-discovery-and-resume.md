# ADR 0010: Session Discovery and Resume

**Date**: 2026-04-07

**Status**: Proposed

## Context

Users struggle to rediscover and resume agent sessions after laptop restarts or when working across multiple project directories. The current session management lacks:

1. **Rich context in listings** - users can't see where sessions were spawned or preview recent activity
2. **Reliable resume** - `cl9 agent continue` fails when current directory differs from spawn directory
3. **Interactive discovery** - no TUI for browsing and selecting sessions
4. **History preview** - can't see recent conversation turns without resuming

### Critical Bug: Working Directory Mismatch

Research into Claude Code's session storage ([session management guide](https://claudelab.net/en/articles/claude-code/claude-code-session-management-resume-guide), [kentgigger.com](https://kentgigger.com/posts/claude-code-conversation-history)) reveals:

- Claude stores sessions in `~/.claude/projects/<encoded-cwd>/*.jsonl`
- `<encoded-cwd>` is the absolute working directory with non-alphanumeric chars replaced by `-`
- Example: `/Users/me/proj/backend` → `-Users-me-proj-backend`
- Resume **requires** being in the same working directory as spawn

**Current cl9 bug** (cli.py:815-838):

```python
def _launch_agent_process(...):
    ...
    current_cwd = Path.cwd().resolve()  # ❌ uses current dir
    process = _spawn_in_project_shell(current_cwd, env, cmd)
```

When resuming a session:
1. User runs `cl9 agent continue` from `/Users/me/proj/tests`
2. Session was originally spawned from `/Users/me/proj/backend`
3. cl9 spawns Claude from current dir `/tests`
4. Claude looks in `~/.claude/projects/-Users-me-proj-tests/` - **not found!**
5. Resume fails or starts fresh session

This is why "continue doesn't work."

### Related Issues

- [Claude Code Issue #24271](https://github.com/anthropics/claude-code/issues/24271) - symlink path comparison issues
- [Claude Code Issue #34318](https://github.com/anthropics/claude-code/issues/34318) - community requests for better CLI session management
- [cc-sessions tool](https://github.com/chronologos/cc-sessions) - third-party tool filling this gap

## Decision

### 1. Restore Working Directory on Resume

When resuming a session, **always spawn the agent from the original `source_cwd`**.

Modify `_launch_agent_process()` in cli.py:

```python
def _launch_agent_process(
    project_root: Path,
    session_id: str,
    session_name: Union[str, None],
    profile: ProfileSpec,
    cmd: List[str],
    spawn_cwd: Optional[Path] = None,  # NEW: explicit spawn directory
) -> None:
    """Launch an agent process from a specific working directory."""

    # Use provided spawn_cwd or fall back to current directory (spawn case)
    effective_cwd = spawn_cwd or Path.cwd().resolve()

    # ... rest of function uses effective_cwd
    process = _spawn_in_project_shell(effective_cwd, env, cmd)
```

Callers:
- `agent_spawn()` - pass `None` (use current directory)
- `agent_continue()` - pass `session.source_cwd` (restore original)
- `agent_fork()` - pass parent's `source_cwd` or current dir (TBD)

This ensures Claude finds the session in `~/.claude/projects/<original-cwd>/`.

### 2. Enhanced Session Listing

Extend `cl9 session list` to show working directories:

```bash
$ cl9 session list

refactor-auth (3 hours ago)
  ID: abc-123-def
  Profile: default (claude)
  Working dir: backend/auth
  Status: idle
  Last used: 2026-04-07 14:23

fix-tests (yesterday)
  ID: xyz-789-ghi
  Profile: default (claude)
  Working dir: tests/
  Status: idle
  Last used: 2026-04-06 09:15
```

Add `--verbose` flag for more detail:
- Full absolute paths
- Process history (how many times spawned)
- Fork relationships

Implementation:
- `sessions.py` already tracks `source_cwd`
- Display as relative path from project root for brevity
- Show absolute path with `--verbose`

### 3. Interactive TUI Session Selector

Add interactive mode when no target specified:

```bash
$ cl9 agent continue    # no argument → TUI
```

Use [Textual](https://github.com/Textualize/textual) for rich TUI:

```
┌─ Select Session ─────────────────────────────────────────┐
│ Search: _                                                 │
├───────────────────────────────────────────────────────────┤
│ > refactor-auth (3 hours ago)                            │
│   backend/auth • default profile                         │
│   Last: "Updated user model to include roles..."         │
│                                                           │
│   fix-tests (yesterday)                                  │
│   tests/ • default profile                               │
│   Last: "Fixed flaky timeout in test_api..."             │
│                                                           │
│   feature-login (2 days ago)                             │
│   frontend/components • default profile                  │
│   Last: "Added password reset flow"                      │
└───────────────────────────────────────────────────────────┘
  ↑/↓: navigate  Enter: select  /: search  q: quit
```

Features:
- Arrow keys for navigation
- `/` or typing for fuzzy search
- Preview of last user message (see #4)
- Enter to resume selected session
- `q` or Escape to cancel

Fallback: If Textual not available, show numbered list + prompt:
```
Sessions:
1. refactor-auth (backend/auth, 3 hours ago)
2. fix-tests (tests/, yesterday)
3. feature-login (frontend/components, 2 days ago)

Select (1-3) or press Enter for latest: _
```

### 4. Session History Preview

Add command to preview session history:

```bash
$ cl9 session show abc-123 --preview
```

This requires reading Claude's session storage:

```python
def get_session_history_preview(
    project_root: Path,
    session_id: str,
    num_turns: int = 3,
) -> List[dict]:
    """Read last N turns from Claude session storage."""
    session = state.get_session(session_id)
    source_cwd = Path(session["source_cwd"])

    # Encode path like Claude does: non-alphanumeric → '-'
    encoded_cwd = ''.join(c if c.isalnum() else '-' for c in str(source_cwd.resolve()))

    session_file = Path.home() / ".claude" / "projects" / encoded_cwd / f"{session_id}.jsonl"

    if not session_file.exists():
        return []

    # Read last N lines from JSONL
    turns = []
    with open(session_file, 'r') as f:
        for line in f:
            turns.append(json.loads(line))

    return turns[-num_turns:]
```

Display format:

```
Session: refactor-auth (abc-123-def)
Location: backend/auth
Last 3 turns:

[You, 14:20]
Update the user model to include role-based permissions

[Claude, 14:21]
I'll update the User model to add role-based permissions. Let me first read...

[You, 14:23]
Also add migration for existing users
```

Use this preview in:
- TUI selector (show last user message)
- `cl9 session show` command
- `cl9 session list --preview` (show first line of last message)

### 5. Working Directory Validation

Before resuming, validate that `source_cwd` still exists:

```python
def validate_session_cwd(session: dict) -> Tuple[bool, str]:
    """Check if session's working directory is valid."""
    source_cwd = Path(session["source_cwd"])

    if not source_cwd.exists():
        return False, f"Directory not found: {source_cwd}"

    if not source_cwd.is_dir():
        return False, f"Not a directory: {source_cwd}"

    # Check if it's still inside the project (for relocatability detection)
    project_root = _find_project_root(source_cwd)
    if project_root is None:
        return False, f"Directory {source_cwd} is not in a cl9 project anymore"

    return True, ""
```

If validation fails, show clear error:

```
Error: Cannot resume session 'refactor-auth'
  Original directory not found: /Users/me/proj/backend/auth

  This might happen if:
  - Directory was deleted
  - Project was moved to a different path
  - Filesystem was remounted

  To delete this stale session: cl9 session delete abc-123 --force
```

### 6. Future: Project Relocatability

**Not implemented in this ADR**, but design should support:

If a project moves from `/Users/me/old-path/` to `/Users/me/new-path/`:

1. **Detection**: When resuming, detect that `source_cwd` doesn't exist but project root moved
2. **Migration offer**: Prompt user to migrate session paths
3. **Implementation options**:
   - Rewrite paths in cl9's `.cl9/state.db`
   - Update Claude's session storage paths (requires rewriting `~/.claude/projects/` structure)
   - Create symlinks from old path to new path
   - Or: just warn and recommend manual migration

For now: validation + clear error messages (see #5).

## Implementation Plan

### Phase 1: Fix Resume Bug (Critical)

1. Add `spawn_cwd: Optional[Path]` parameter to `_launch_agent_process()`
2. Update `agent_continue()` to pass `session.source_cwd`
3. Update `agent_fork()` to pass parent's `source_cwd`
4. Add tests for working directory restoration

### Phase 2: Enhanced Listing

1. Update `cl9 session list` to display `source_cwd` (relative paths)
2. Add `--verbose` flag for absolute paths + process history
3. Update `sessions.py` if needed (already has `source_cwd`)

### Phase 3: Session Preview

1. Implement `get_session_history_preview()` function
2. Add `cl9 session show <target>` command
3. Add `--preview` flag to show conversation turns
4. Handle missing session files gracefully

### Phase 4: Interactive TUI

1. Add `textual` as optional dependency
2. Implement TUI selector with:
   - Session list display
   - Fuzzy search
   - Preview pane (last message)
   - Keyboard navigation
3. Update `agent_continue()` to invoke TUI when no target given
4. Add fallback numbered list UI if Textual unavailable

### Phase 5: Validation & Errors

1. Implement `validate_session_cwd()` before resume
2. Add clear error messages for common failure modes
3. Add warnings for moved projects (relocatability detection)

## UI/UX Design Details

### Command Variants

```bash
# Existing (unchanged)
cl9 agent continue                    # resume latest session
cl9 agent continue abc-123           # resume by ID
cl9 agent continue refactor-auth     # resume by name

# New in this ADR
cl9 agent resume                     # alias for continue (no args → TUI)
cl9 session list                     # enhanced with working dirs
cl9 session list --verbose           # full details
cl9 session list --preview           # include last message preview
cl9 session show abc-123             # detailed session info
cl9 session show abc-123 --preview   # include conversation turns
```

### TUI Keybindings

- `↑`/`↓` or `j`/`k` - navigate list
- `Enter` - select and resume session
- `/` - focus search box
- `Esc` - clear search / exit
- `q` - quit without selecting
- `?` - show help

### Output Formatting

Use [Rich](https://github.com/Textualize/rich) for terminal formatting:
- Relative timestamps ("3 hours ago", "yesterday")
- Syntax highlighting for session IDs (dim gray)
- Status indicators (🟢 running, ⚪ idle, 🔴 stale)
- Tree view for fork relationships

## Dependencies

### Required

- No new required dependencies (use stdlib)
- `source_cwd` already tracked in `sessions.py`

### Optional

- `textual>=0.50.0` - for rich TUI (phase 4)
- `rich>=13.0.0` - for formatted output (already used in cl9?)
- `rapidfuzz>=3.0.0` - for fuzzy search in TUI

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
tui = [
    "textual>=0.50.0",
    "rich>=13.0.0",
    "rapidfuzz>=3.0.0",
]
```

Install with: `pip install cl9[tui]`

If not installed, fall back to numbered list UI.

## Consequences

### Positive

- **Resume works reliably** - spawning from correct directory fixes Claude session lookup
- **Better session discovery** - users can see where sessions were spawned
- **Faster workflow** - TUI eliminates need to remember session IDs
- **Reduced friction** - preview helps identify the right session quickly
- **Clear error messages** - users understand why resume fails

### Negative

- **Complexity** - TUI adds significant code and dependency
- **Optional dependency** - need fallback UI for users without Textual
- **Testing surface** - TUI interaction harder to test than CLI
- **Performance** - reading session history from disk adds latency

### Trade-offs Accepted

- TUI is optional (extra dependency) - simpler numbered list fallback
- Preview reads from Claude's storage - couples to Claude's format
- Path encoding logic duplicates Claude's implementation - necessary for compatibility
- Relocatability deferred - validation + errors sufficient for now

## Security Considerations

- **Session storage access** - reading `~/.claude/projects/` is read-only, no writes
- **Path traversal** - validate encoded paths before file access
- **Sensitive data** - session previews may contain sensitive info, don't log them

## Testing Strategy

### Unit Tests

- `validate_session_cwd()` with various path states
- Path encoding matches Claude's format
- Session history parsing from JSONL

### Integration Tests

- Spawn → Continue from different directory (critical)
- Fork → Continue preserves working directory
- Session list shows correct relative paths
- Preview reads actual session files

### Manual Testing

- TUI navigation and search
- Fallback UI when Textual unavailable
- Error messages for edge cases
- Preview formatting and truncation

## Rollout Plan

1. **Phase 1 (critical bugfix)** - merge immediately, fixes resume
2. **Phase 2 (enhanced listing)** - low risk, improves discoverability
3. **Phase 3 (preview)** - depends on Claude storage format stability
4. **Phase 4 (TUI)** - optional, staged rollout, gather feedback
5. **Phase 5 (validation)** - polish, better errors

Each phase is independently useful and can ship separately.

## Future Enhancements (Out of Scope)

- **Session history search** - grep across all session transcripts
- **Session tags** - user-defined tags for categorization
- **Session export** - save session to file for sharing/backup
- **Cross-project session search** - find sessions across all registered projects
- **Session analytics** - usage patterns, most active sessions
- **Project relocatability** - automatic path rewriting on project move

## References

- GitHub Issue #3: Poor session overview and resume UX (to be created)
- [Claude Code Session Management Guide](https://claudelab.net/en/articles/claude-code/claude-code-session-management-resume-guide)
- [How to resume Claude Code conversations](https://kentgigger.com/posts/claude-code-conversation-history)
- [Claude Code Issue #24271](https://github.com/anthropics/claude-code/issues/24271) - Symlink resume failures
- [Claude Code Issue #34318](https://github.com/anthropics/claude-code/issues/34318) - Feature requests
- [cc-sessions tool](https://github.com/chronologos/cc-sessions) - Community solution
- [Textual](https://github.com/Textualize/textual) - Python TUI framework
- [Rich](https://github.com/Textualize/rich) - Terminal formatting
