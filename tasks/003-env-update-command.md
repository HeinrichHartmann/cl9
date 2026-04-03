# Task 003: Environment Update Command

**Status**: Done
**Priority**: Medium
**Type**: Feature

## Overview

Add `cl9 env` command group with `init` (alias) and `update` subcommands. Track template state to enable safe updates.

## CLI Structure

```
cl9 env init [<path>] [-n/--name] [-t/--type]   # Alias for cl9 init
cl9 env update [--diff] [--force]               # Update environment from template
```

Note: `cl9 init` remains as primary command. `cl9 env init` is an alias for discoverability.

## State Tracking

On `cl9 init`, create `.cl9/env/state.json`:

```json
{
  "type": "default",
  "version": "1",
  "applied_at": "2026-04-03T10:00:00Z",
  "files": {
    "README.md": "sha256:abc123...",
    "MEMORY.md": "sha256:def456...",
    "flake.nix": "sha256:789xyz...",
    ".envrc": "sha256:..."
  }
}
```

- `type`: Environment type used
- `files`: Map of relative path → hash of file as delivered by template

## Update Logic

`cl9 env update`:

1. Load `.cl9/env/state.json` (error if missing: "not initialized with environment tracking")
2. Get template for `state.type`
3. For each file in template:
   - **New file** (not in state) → add it
   - **Unchanged** (current hash == state hash) → overwrite with new template
   - **User modified** (current hash != state hash) → skip + warn
4. Update `.cl9/env/state.json` with new hashes
5. Report summary

### Flags

**`--diff`** (dry run):
- Show what would be added/updated/skipped
- Don't modify any files

**`--force`**:
- Overwrite user-modified files
- Useful when user wants to reset to template defaults

## Output Examples

**Normal update:**
```
Updating environment (type: default)

  Updated:  flake.nix
  Updated:  .envrc
  Added:    .cl9/hooks/enter.sh
  Skipped:  README.md (modified by user)
  Skipped:  MEMORY.md (modified by user)

2 files updated, 1 added, 2 skipped (use --force to overwrite)
```

**With --diff:**
```
Dry run - no files will be modified

  Would update:  flake.nix
  Would update:  .envrc
  Would add:     .cl9/hooks/enter.sh
  Would skip:    README.md (modified by user)
  Would skip:    MEMORY.md (modified by user)
```

**With --force:**
```
Updating environment (type: default) [FORCE]

  Updated:  flake.nix
  Updated:  .envrc
  Updated:  README.md (was modified)
  Updated:  MEMORY.md (was modified)
  Added:    .cl9/hooks/enter.sh

5 files updated, 0 skipped
```

## Implementation

### 1. Update `cli.py`

```python
@main.group()
def env():
    """Environment management commands."""
    pass

@env.command('init')
@click.argument('path', required=False, default='.')
@click.option('-n', '--name', help='Project name')
@click.option('-t', '--type', 'env_type', help='Environment type')
@click.pass_context
def env_init(ctx, path, name, env_type):
    """Initialize a cl9 project (alias for 'cl9 init')."""
    ctx.invoke(init, path=path, name=name, env_type=env_type)

@env.command('update')
@click.option('--diff', is_flag=True, help='Show what would change (dry run)')
@click.option('--force', is_flag=True, help='Overwrite user-modified files')
def env_update(diff, force):
    """Update environment from template."""
    ...
```

### 2. Update `environments/__init__.py`

Add functions:
```python
import hashlib

def hash_file(path: Path) -> str:
    """Compute SHA256 hash of file."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

def save_state(project_path: Path, env_type: str, files: dict[str, str]):
    """Save environment state to .cl9/env/state.json."""
    state_dir = project_path / ".cl9" / "env"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "type": env_type,
        "version": "1",
        "applied_at": datetime.now().isoformat(),
        "files": files
    }
    (state_dir / "state.json").write_text(json.dumps(state, indent=2))

def load_state(project_path: Path) -> dict | None:
    """Load environment state from .cl9/env/state.json."""
    state_file = project_path / ".cl9" / "env" / "state.json"
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())
```

### 3. Update `cl9 init` to Save State

After applying environment template:
```python
# Track delivered files
delivered_files = {}
for file_path in copied_files:
    rel_path = file_path.relative_to(project_path)
    delivered_files[str(rel_path)] = hash_file(file_path)

save_state(project_path, env_type, delivered_files)
```

## Files to Modify

- `src/cl9/cli.py` - Add `env` group with `init` alias and `update` command
- `src/cl9/environments/__init__.py` - Add hash/state functions
- Update `cl9 init` to call `save_state()`

## Files Created by Init

After this change, `cl9 init` creates:
```
project/
├── .cl9/
│   ├── config.json         # Existing
│   └── env/
│       └── state.json      # NEW: tracks template state
├── src/
├── doc/
├── data/
├── README.md
├── MEMORY.md
├── flake.nix
└── .envrc
```

## Completion Criteria

- [x] `cl9 env init` works as alias for `cl9 init`
- [x] `cl9 init` creates `.cl9/env/state.json` with file hashes
- [x] `cl9 env update` updates unchanged files from template
- [x] `cl9 env update` skips user-modified files with warning
- [x] `--diff` shows what would change without modifying
- [x] `--force` overwrites user-modified files
- [x] Clear output showing what was updated/skipped

## Edge Cases

- Project initialized before state tracking → error with helpful message
- Template file deleted by user → re-add on update
- Environment type no longer exists → error with message

---

**When done**: Update this file, set Status to "Done", and note any implementation decisions.

## Implementation Notes

- State is written on every `cl9 init`, including `minimal`, under `.cl9/env/state.json`.
- State tracks delivered files only; empty managed directories are recreated by environment logic, not hashed.
- `cl9 env update` safely skips user-modified tracked files unless `--force` is provided.
- If a template introduces a new file that already exists in the project but was never tracked, update skips it rather than overwriting silently.
- The existing shell completion entrypoints remain available as `cl9 env bash`, `cl9 env zsh`, and `cl9 env fish`.
