# ADR 0004: Environment Types for Project Initialization

**Date**: 2026-04-03

**Status**: Accepted

## Context

When initializing a cl9 project, users need a consistent starting point with sensible defaults. Different workflows require different tooling (Nix+direnv, Python venv, Docker, etc.). We need a flexible system that:

1. Provides a useful default out of the box
2. Allows customization without modifying cl9 core
3. Keeps cl9 itself dependency-free (no hard requirements on Nix, direnv, etc.)

## Terminology

**Environment Type**: A template that defines the initial structure and configuration files for a cl9 project. Environment types are just folders containing files to be copied. They are not plugins or code - just static templates.

## Decision

### Environment Type System

Environment types are template directories that `cl9 init` copies into new projects. The system is intentionally simple:

1. An environment type is a folder containing files/directories to copy
2. `cl9 init --type <type>` selects which template to use
3. A global default can be configured
4. cl9 ships with one built-in type: `default`

### Template Resolution Order

When `cl9 init --type foo` is called:

1. Check built-in types (shipped with cl9)
2. Check user types in `~/.config/cl9/environments/`
3. Check if `foo` is a local path to a directory
4. (Future) Check if `foo` is a git URL or GitHub shorthand

### The Default Environment Type

cl9 ships with a `default` environment type designed for Nix+direnv workflows:

```
default/
├── src/                    # Source code checkouts
├── doc/                    # Documents (Google Docs exports, notes, etc.)
├── data/                   # Data artifacts (CSVs, SQLite, downloads)
├── README.md               # Project readme (template)
├── MEMORY.md               # Agent memory / project context
├── flake.nix               # Nix flake for tool management
└── .envrc                  # direnv configuration
```

**Directory Purposes:**

| Directory | Purpose |
|-----------|---------|
| `src/` | Source code repositories. Policy: deliberate copies, not symlinks. Same repo can exist in multiple projects. |
| `doc/` | Human-readable documents. Google Docs exports, design docs, notes. |
| `data/` | Data artifacts managed by the project. CSVs, SQLite databases, downloaded files. |

**Key Files:**

| File | Purpose |
|------|---------|
| `README.md` | Project overview for humans. Template with project name placeholder. |
| `MEMORY.md` | Persistent context for agents. Project-specific instructions and state. |
| `flake.nix` | Nix flake declaring project tools. Use `nix search nixpkgs <tool>` to find packages. |
| `.envrc` | direnv config. Loads flake, holds env vars, can contain secrets (gitignored). |

### Template Variable Substitution

Templates support simple variable substitution:

- `{{PROJECT_NAME}}` - Replaced with project name
- `{{PROJECT_PATH}}` - Replaced with absolute project path
- `{{DATE}}` - Replaced with initialization date

### Dependencies and Requirements

**cl9 has no hard dependencies** on Nix, direnv, or any other tool. The environment type declares its own requirements.

The `default` environment type requires:
- **Nix** (with flakes enabled) - for reproducible tool management
- **direnv** - for automatic environment loading

Users without these tools can:
1. Use a different environment type (`cl9 init --type minimal`)
2. Create their own environment type
3. Ignore the generated files

### CLI Changes

```
cl9 init [<path>] [-n/--name <name>] [-t/--type <type>]
cl9 env init ...              # Alias for cl9 init
cl9 env update [--diff] [--force]
```

- `--type`: Environment type to use. Defaults to configured global default, or `default` if not set.
- `cl9 init --type minimal` - Bare minimum (just `.cl9/` directory)
- `cl9 init --type default` - Full Nix+direnv setup

### State Tracking

On init, cl9 records delivered files in `.cl9/env/state.json`:

```json
{
  "type": "default",
  "version": "1",
  "applied_at": "2026-04-03T10:00:00Z",
  "files": {
    "README.md": "sha256:abc123...",
    "flake.nix": "sha256:def456..."
  }
}
```

This enables `cl9 env update` to:
- Update files unchanged from template (hash matches original)
- Skip user-modified files with warning
- `--force` to overwrite user changes anyway
- `--diff` for dry-run preview

### Configuration

Global default type in `~/.config/cl9/config.json`:

```json
{
  "default_environment_type": "default"
}
```

### Future Extensions (Not Implemented Now)

The design accommodates future extensions:

1. **Git-based types**: `cl9 init --type github:user/repo/path`
2. **Type registry**: `cl9 env-type list`, `cl9 env-type add <name> <source>`
3. **Post-init hooks**: Scripts in environment type that run after copying
4. **Type inheritance**: Types that extend other types

These are explicitly out of scope for initial implementation.

## Environment Type vs Plugins

| Aspect | Environment Types | Plugins |
|--------|-------------------|---------|
| Purpose | Project scaffolding | Runtime behavior |
| Format | Static folder templates | Python code |
| When used | `cl9 init` only | Throughout lifecycle |
| Example | Nix+direnv files | tmux window management |

The tmux integration remains a plugin (runtime behavior), not an environment type (static files).

## Consequences

### Positive

- Simple mental model: environment types are just folders
- No dependencies in cl9 core
- Users can create custom types trivially (just copy a folder)
- Default provides a complete, opinionated starting point
- Future extensibility without breaking changes

### Negative

- Users must have Nix+direnv for default type to be useful
- No validation that environment requirements are met
- Template variables are limited (no logic, conditionals)

### Trade-offs Accepted

- Simplicity over power: Templates are static, not programmable
- Convention over configuration: Default type makes assumptions
- Flexibility over integration: cl9 doesn't know/care what tools the environment uses

## File Locations

Built-in environment types:
```
src/cl9/environments/
├── default/
│   ├── README.md
│   ├── MEMORY.md
│   ├── flake.nix
│   └── .envrc
└── minimal/               # Logical built-in type with no template files
```

Empty directories like `src/`, `doc/`, and `data/` are created programmatically during initialization rather than tracked with placeholder files.
The `minimal` type may be handled as a special built-in case in code, since empty directories are not preserved in package distributions.

User environment types:
```
~/.config/cl9/environments/
└── my-custom-type/
    └── ...
```
