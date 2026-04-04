# ADR 0007: Project Tools and Dependencies

**Date**: 2026-04-04

**Status**: Accepted

## Context

cl9 needs to support project-specific tooling (backup, linting, deployment, etc.) without bloating the core with external dependencies.

The question arose: should cl9 bundle tools like restic, or require them globally, or have a plugin system? How should cl9 handle the tension between wanting rich features and staying minimal?

## Decision

### Templates as Capability Packages

Templates are not just scaffolding. They are **capability bundles** that can provide:

1. **Structure** - directories, config files
2. **Tools** - via flake.nix (available through direnv)
3. **Commands** - shell scripts in `bin/`

This shifts responsibility for tool availability to the template and project environment, keeping cl9 core minimal.

Templates do **not** own `.cl9/config.json`. That file remains cl9-managed project metadata. If templates need their own metadata in the future, it should live in a separate file or namespaced structure.

### The bin/ Convention

Every cl9 project can have a `bin/` directory at the project root. This directory is automatically prepended to PATH when:

- `cl9 project enter` spawns a subshell
- `cl9 agent spawn/continue/fork` launches an agent
- `cl9 project run` executes a command

```
my-project/
├── bin/
│   ├── snapshot      # restic wrapper
│   ├── backup        # remote backup
│   ├── lint          # project linting
│   └── deploy        # deployment script
├── .cl9/
├── flake.nix         # Provides restic, etc.
└── ...
```

### cl9 project run

A new command to run commands inside the project environment from anywhere in the project tree:

```bash
cl9 run snapshot                    # Alias
cl9 project run backup --remote s3  # Full form
```

Behavior:
- Finds project root (walks up to `.cl9/`)
- Sets CWD to project root
- Prepends `bin/` to PATH
- Sets CL9_* environment variables
- Uses the same effective environment model as `cl9 project enter`
- Executes the requested command in that environment

`cl9 project run` is not limited to `bin/` commands. `bin/` is one source of executables, but commands may also come from the surrounding project environment (for example tools provided via Nix+direnv).

### Environment Setup

All project-context commands (`enter`, `run`, `agent spawn`, etc.) set:

```bash
PATH="$PROJECT_ROOT/bin:$PATH"
CL9_PROJECT="project-name"
CL9_PROJECT_PATH="/absolute/path/to/project"
CL9_ACTIVE="1"
```

The effective command resolution environment should match what a user would see after entering the project context. In practice this means `cl9` should respect project-local environment setup such as direnv/Nix when establishing the command environment, rather than acting as a separate ad hoc launcher.

### Agent Tool Resolution

Agent commands also execute in the project environment.

This is intentional: if a template or project environment provides `claude`, related wrappers, or supporting tools, `cl9` should use that environment rather than insisting on a separately managed global install.

In other words, agent tool resolution is part of the project's capability bundle, not something `cl9` core manages independently.

### Example: Snapshot Command

A template can provide backup/snapshot functionality:

**flake.nix** (tools):
```nix
{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }: {
    devShells.default = pkgs.mkShell {
      buildInputs = [ pkgs.restic ];
    };
  };
}
```

**bin/snapshot** (command):
```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="${CL9_SNAPSHOT_REPO:-$HOME/.local/share/cl9/snapshots}"
PROJECT_ROOT="${CL9_PROJECT_PATH:-.}"

# Initialize repo if needed
if ! restic -r "$REPO" snapshots &>/dev/null; then
    restic -r "$REPO" init
fi

restic -r "$REPO" backup "$PROJECT_ROOT" \
    --exclude=node_modules \
    --exclude=.git \
    --exclude='*.pyc' \
    --tag "project:$CL9_PROJECT"

echo "Snapshot created for $CL9_PROJECT"
```

**Usage**:
```bash
cd my-project
snapshot                    # In project shell
cl9 run snapshot            # From anywhere in project
```

### cl9 Core Remains Minimal

cl9 core has no external tool dependencies beyond Python stdlib + click.

| Tier | What | Dependencies |
|------|------|--------------|
| Core | project/agent/session management | Python only |
| Template-provided | snapshot, backup, lint, etc. | Tools from flake.nix |

If a `bin/` script requires a tool that's not available:
- The script fails with a clear error
- User adds the tool to their flake.nix
- No cl9 code changes needed

### Templates Can Document Their Capabilities

Templates should include a README or manifest listing what they provide:

```
templates/default/
├── README.md           # Documents: includes snapshot, backup commands
├── bin/
│   ├── snapshot
│   └── backup
├── flake.nix           # Declares: restic, jq, etc.
└── ...
```

## Consequences

### Positive

- cl9 core stays simple and dependency-free
- Templates are powerful and self-contained
- Project-scoped commands feel native
- No plugin system complexity
- Users can add/modify `bin/` scripts freely
- Different templates = different capabilities

### Negative

- Features depend on which template was used
- Discoverability: user must know what `bin/` contains
- Scripts must handle missing tools gracefully
- Less unified than built-in commands

### Trade-offs Accepted

- Convention over configuration: `bin/` is always on PATH
- Template responsibility over core features
- Shell scripts over Python plugins (simpler, more flexible)
- Per-project capabilities over global cl9 features

## Future Considerations

### Capability Discovery

Could add `cl9 project capabilities` or `cl9 project commands` to list what's available:

```bash
$ cl9 project commands
Commands available in bin/:
  snapshot    Create a restic snapshot
  backup      Backup to remote storage
  lint        Run project linters
```

This would scan `bin/` and optionally read metadata from scripts.

### Template Inheritance

Templates could extend other templates:

```yaml
# templates/python-ml/manifest.yaml
extends: default
```

Not implemented now, but the design supports it.

### Remote Templates

```bash
cl9 init --type github:myorg/cl9-templates/python-ml
```

Future enhancement, not in initial scope.
