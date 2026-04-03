# Task 002: Implement Environment Types for Project Initialization

**Status**: Done
**Priority**: High
**Type**: Feature

## Overview

Implement the environment type system as designed in ADR 0004. This adds scaffolding/templates to `cl9 init`.

## Required Changes

### 1. Create Environment Types Directory Structure

```
src/cl9/environments/
├── __init__.py           # Package marker + helper functions
├── default/
│   ├── README.md
│   ├── MEMORY.md
│   ├── flake.nix
│   └── .envrc
└── minimal/              # Logical built-in type; only .cl9/ created
```

### 2. Template File Contents

**README.md:**
```markdown
# {{PROJECT_NAME}}

*Please write a brief description of what this project is about.*

## Project Structure

This project uses the cl9 default environment type (Nix + direnv).

| Directory | Purpose |
|-----------|---------|
| `src/` | Source code checkouts. Policy: deliberate copies, same repo can exist in multiple projects. |
| `doc/` | Human-readable documents. Google Docs exports, design notes, etc. |
| `data/` | Data artifacts. CSVs, SQLite databases, downloaded files. |

## Environment

This project depends on:
- **Nix** (with flakes enabled) - for reproducible tool management
- **direnv** - for automatic environment loading

### Adding Tools

Edit `flake.nix` to add tools. Search available packages:
```bash
nix search nixpkgs <tool>
```

Over 100,000 packages available.

### Environment Variables

Add variables to `.envrc`. Secrets can go here (it's gitignored by default).

### Entering the Project

```bash
cd {{PROJECT_PATH}}
direnv allow    # First time only
```

Or use cl9:
```bash
cl9 enter {{PROJECT_NAME}}
```
```

**MEMORY.md:**
```markdown
# {{PROJECT_NAME}} - Agent Memory

*This file provides persistent context for AI agents working on this project.*

## Project Purpose

[Describe what this project aims to accomplish]

## Key Decisions

[Record important decisions and their rationale]

## Current State

[What is the current status? What's working, what's not?]

## Notes

[Anything else agents should know]
```

**flake.nix:**
```nix
{
  description = "{{PROJECT_NAME}} - cl9 project environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Add your tools here
            # Example:
            # git
            # jq
            # python3
          ];

          shellHook = ''
            echo "{{PROJECT_NAME}} environment loaded"
          '';
        };
      });
}
```

**.envrc:**
```bash
# {{PROJECT_NAME}} environment configuration
# This file is loaded by direnv when entering the project directory

# Load Nix flake environment
use flake

# Project-specific environment variables
# export API_KEY="..."
# export DATABASE_URL="..."

# Secrets (keep this file out of version control if adding secrets)
```

### 3. Update `cl9 init` Command

Add `--type/-t` option:

```python
@main.command()
@click.argument('path', required=False, default='.')
@click.option('-n', '--name', help='Project name (default: directory name)')
@click.option('-t', '--type', 'env_type', default=None,
              help='Environment type (default: from config or "default")')
def init(path, name, env_type):
    ...
```

**Logic:**
1. Resolve path to absolute
2. Determine project name (from `--name` or directory name)
3. Determine environment type:
   - Use `--type` if provided
   - Else use `config.default_environment_type` if set
   - Else use `"default"`
4. Create `.cl9/` directory
5. Create any environment-defined directories (for `default`: `src/`, `doc/`, `data/`)
6. Copy environment type template files (with variable substitution)
7. Register in global registry

### 4. Environment Type Loader

Create `src/cl9/environments/__init__.py`:

```python
"""Environment type management."""

from pathlib import Path
from typing import Optional
import shutil
from datetime import date

BUILTIN_DIR = Path(__file__).parent

def get_environment_path(type_name: str, user_env_dir: Path) -> Optional[Path]:
    """Resolve environment type to path.

    Resolution order:
    1. Built-in types
    2. User types in ~/.config/cl9/environments/
    3. Local path (if exists and is directory)
    """
    # Built-in
    builtin = BUILTIN_DIR / type_name
    if builtin.is_dir():
        return builtin

    # User types
    user = user_env_dir / type_name
    if user.is_dir():
        return user

    # Local path
    local = Path(type_name)
    if local.is_dir():
        return local.resolve()

    return None

def apply_environment(env_path: Path, target_path: Path, variables: dict):
    """Copy environment template to target with variable substitution.

    Args:
        env_path: Path to environment type template
        target_path: Project directory to copy into
        variables: Dict of {{VAR}} -> value substitutions
    """
    for item in env_path.rglob('*'):
        if item.is_file():
            rel_path = item.relative_to(env_path)
            dest = target_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Check if text file (for substitution)
            try:
                content = item.read_text()
                for var, value in variables.items():
                    content = content.replace(f'{{{{{var}}}}}', value)
                dest.write_text(content)
            except UnicodeDecodeError:
                # Binary file - copy directly
                shutil.copy2(item, dest)

def list_builtin_types() -> list[str]:
    """List available built-in environment types."""
    return [d.name for d in BUILTIN_DIR.iterdir()
            if d.is_dir() and not d.name.startswith('_')]

def get_environment_directories(type_name: str) -> list[str]:
    """Return directories to create for a built-in environment type."""
    if type_name == "default":
        return ["src", "doc", "data"]
    return []
```

### 5. Update Config for Default Type

Add to `config.py`:

```python
@property
def environments_dir(self) -> Path:
    """User environment types directory."""
    env_dir = self.config_dir / "environments"
    env_dir.mkdir(parents=True, exist_ok=True)
    return env_dir

def get_default_environment_type(self) -> str:
    """Get configured default environment type."""
    config = self.load_global_config()
    return config.get('default_environment_type', 'default')
```

### 6. Add `cl9 init --type list` or `cl9 types` Command

Optional but useful:

```bash
cl9 types              # List available environment types
cl9 types show default # Show contents of a type
```

## Files to Create

- `src/cl9/environments/__init__.py`
- `src/cl9/environments/default/README.md`
- `src/cl9/environments/default/MEMORY.md`
- `src/cl9/environments/default/flake.nix`
- `src/cl9/environments/default/.envrc`

## Files to Modify

- `src/cl9/cli.py` - Add `--type` to init, optionally add `types` command
- `src/cl9/config.py` - Add environments_dir, get_default_environment_type
- `README.md` - Document new `--type` option
- `MANIFEST.in` or `pyproject.toml` - Ensure template files are included in package

## Completion Criteria

- [ ] Environment types directory structure created
- [x] Default environment template files created with correct content
- [x] Minimal environment type created (empty)
- [x] `cl9 init --type <type>` works
- [x] Variable substitution ({{PROJECT_NAME}}, etc.) works
- [x] Empty directories for the default environment are created without placeholder files
- [x] Falls back to configured default, then "default"
- [x] Template files included in package distribution
- [x] README.md updated

## Testing

- `cl9 init --type default` creates all expected files
- `cl9 init --type minimal` creates only `.cl9/`
- `cl9 init` (no type) uses default
- `cl9 init --type default` creates empty `src/`, `doc/`, and `data/` directories with no placeholder files
- Variables substituted correctly in generated files
- Binary files (if any) copied without corruption

---

**When done**: Update this file, set Status to "Done", and note any implementation decisions.

## Implementation Notes

- The `default` environment is file-backed under `src/cl9/environments/default/`.
- The `minimal` environment is handled as a logical built-in type with no template files.
- Empty `src/`, `doc/`, and `data/` directories are created programmatically during initialization.
- `cl9 init` now fails before writing if any generated path already exists and asks the user to move conflicting paths out of the way.
- Variable substitution supports `{{PROJECT_NAME}}`, `{{PROJECT_PATH}}`, and `{{DATE}}`.
