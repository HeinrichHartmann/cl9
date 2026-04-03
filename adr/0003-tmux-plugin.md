# ADR 0003: Tmux Integration Plugin

**Date**: 2026-03-27

**Status**: Accepted

## Context

Users frequently work with tmux to manage multiple terminal sessions. When entering a cl9 project, it's beneficial to automatically set up a tmux window with a split layout optimized for AI-assisted development:
- Top pane (larger): For running the LLM agent
- Bottom pane (smaller): For shell commands and navigation

This integration should:
- Work seamlessly with existing tmux sessions
- Not interfere with user's tmux workflow
- Be optional and fail gracefully if tmux is unavailable
- Provide a consistent, predictable layout

## Decision

Implement tmux integration as a **built-in plugin** that ships with cl9.

### Design Principles

1. **Respectful of existing sessions**: Don't create tmux sessions. Only create windows within the current session.

2. **Window ownership model**: cl9 owns windows it creates (identified by `cl9:` prefix). We can modify/update these windows. Sessions remain user-managed.

3. **Graceful degradation**: If tmux is not available or user is not in a tmux session, fall back to normal subshell behavior.

4. **Hard-coded defaults**: No configuration in initial version. Use sensible defaults that work for most users. Configuration can be added later.

### Technical Specifications

**Plugin Location**: `src/cl9/plugins/builtin/tmux.py`

**Activation Conditions**:
1. Plugin loads successfully (tmux executable found on PATH)
2. User is currently in a tmux session (`$TMUX` env var exists)

**Behavior on `cl9 enter <target>`**:

1. **Check for existing window**:
   - Look for window named `cl9:{project_name}` in current session
   - If exists: Switch to that window (no modification)
   - If not exists: Create new window (see below)

2. **Create window layout**:
   ```
   ┌─────────────────────────────┐
   │  Top Pane (75%)             │
   │  Shell in project dir       │
   │  (For running cl9 agent)    │
   ├─────────────────────────────┤
   │  Bottom Pane (25%)          │
   │  Shell in project dir       │
   │  (For commands)             │
   └─────────────────────────────┘
   ```

3. **Window configuration**:
   - Name: `cl9:{project_name}` (e.g., `cl9:myapp`)
   - Working directory: Project path (both panes)
   - Environment: CL9_PROJECT, CL9_PROJECT_PATH, CL9_ACTIVE=1 (both panes)
   - Initial focus: Top pane

4. **Takes over execution**: Returns `True` from `on_enter` hook, preventing default subshell spawn.

### Hard-Coded Constants

```python
# Window naming
WINDOW_PREFIX = "cl9:"

# Layout
SPLIT_ORIENTATION = "horizontal"  # Top/bottom split
TOP_PANE_PERCENTAGE = 75
BOTTOM_PANE_PERCENTAGE = 25

# Focus
INITIAL_FOCUS_PANE = "top"  # 0 = top, 1 = bottom

# Behavior
REUSE_EXISTING_WINDOW = True
```

### Plugin Lifecycle

**On Load (Plugin Initialization)**:
```python
def __init__(config):
    # Test if tmux is available
    if not shutil.which('tmux'):
        raise PluginLoadError("tmux executable not found on PATH")

    # Test if we can communicate with tmux
    try:
        subprocess.run(['tmux', 'display-message'],
                      capture_output=True, check=True, timeout=1)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # It's OK if not in tmux session, just note it
        pass
```

**On Enter**:
```python
def on_enter(project_data, env, config):
    # Only activate if in tmux session
    if not os.environ.get('TMUX'):
        return False  # Not in tmux, use default behavior

    window_name = f"cl9:{project_data['name']}"

    if _window_exists(window_name) and REUSE_EXISTING_WINDOW:
        _switch_to_window(window_name)
    else:
        _create_window_with_split(window_name, project_data, env)

    return True  # Took over - don't spawn subshell
```

### Tmux Commands Used

```bash
# Check if window exists
tmux list-windows -F '#{window_name}'

# Switch to window
tmux select-window -t <window_name>

# Create new window
tmux new-window -n <window_name> -c <project_path>

# Split window horizontally (top/bottom)
tmux split-window -v -p <bottom_percentage> -c <project_path>

# Select pane
tmux select-pane -t <pane_index>

# Set environment variables (optional, may use shell env instead)
tmux set-environment <KEY> <VALUE>
```

### Window Ownership

Windows with `cl9:` prefix are "owned" by cl9:
- cl9 created them and can modify them
- cl9 can update layout if design changes
- cl9 can clean them up if needed
- Users can manually close them (cl9 will recreate on next enter)

Windows without `cl9:` prefix are user-managed:
- cl9 never modifies them
- cl9 never deletes them

Sessions are always user-managed:
- cl9 never creates sessions
- cl9 never deletes sessions
- cl9 only operates within the current session

### Error Handling

**Plugin fails to load**:
- Log warning: "tmux plugin not available: tmux executable not found"
- Continue cl9 startup normally
- Plugin is inactive, default behavior used

**Not in tmux session**:
- Plugin loaded but `is_enabled()` returns False
- Fall back to default subshell behavior
- No error or warning (this is normal)

**Tmux command fails**:
- Log error with command details
- Return False from hook (fall back to default)
- Don't crash cl9

## Consequences

### Positive

- **Seamless integration**: Works transparently when in tmux
- **Non-intrusive**: Doesn't interfere when not in tmux
- **Predictable layout**: Consistent split for all projects
- **Efficient workflow**: Agent and shell in same window
- **Safe**: Only operates within current session
- **Simple**: No configuration to manage

### Negative

- **Hard-coded layout**: Can't customize without editing code (acceptable for v1)
- **Tmux-only**: Doesn't work with other terminal multiplexers (could add plugins later)
- **No session management**: Users must manage sessions themselves (acceptable - simpler model)

### Trade-offs Accepted

- Hard-coded defaults vs. flexibility: Choose simplicity for v1, can add config later
- Window-only vs. session management: Choose safer, simpler window-only model
- Single multiplexer vs. multiple: Focus on tmux first, most common use case

## Alternatives Considered

### Option 1: Create tmux sessions per project

**Rejected because:**
- More complex (session naming conflicts, attachment logic)
- Interferes with user's session management
- Harder to integrate with existing workflows
- Users may want multiple projects in one session

### Option 2: Support multiple terminal multiplexers

**Rejected for v1 because:**
- Tmux is most common
- Can add zellij, screen, etc. as separate plugins later
- Keep initial implementation focused

### Option 3: Configuration-driven layouts

**Rejected for v1 because:**
- Adds complexity without proven need
- Hard-coded 75/25 split works well for most cases
- Can add configuration in v2 based on user feedback

### Option 4: Attach to existing panes instead of windows

**Rejected because:**
- Windows provide cleaner isolation
- Easier to track what cl9 owns (window names)
- More predictable behavior

## Implementation Notes

### Testing Strategy

1. **Unit tests**:
   - Window existence detection
   - Window name generation
   - Command construction

2. **Integration tests** (manual):
   - Create window when not exists
   - Switch to window when exists
   - Verify both panes in correct directory
   - Verify environment variables set
   - Verify focus on correct pane

3. **Fallback tests**:
   - Behavior when not in tmux
   - Behavior when tmux not installed
   - Behavior when tmux command fails

### Future Enhancements (Not in v1)

- Configuration support (split ratio, focus pane, etc.)
- Command to clean up all cl9 windows
- Command to list all cl9 windows
- Auto-start agent in top pane (optional)
- Support for other terminal multiplexers (zellij, screen)
- Custom layouts per project (via project-local config)

## References

- tmux man page: `man tmux`
- tmux scripting: https://leanpub.com/the-tao-of-tmux/read
- Similar tools: tmuxinator, tmuxp (for comparison)
