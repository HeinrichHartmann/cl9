# ADR 0001: Mission and Initial Design

**Date**: 2026-03-26

**Status**: Accepted

## Mission

`cl9` (Claw 9) is an opinionated LLM session manager for organizing and context-switching between AI-assisted work sessions. The name references Plan 9, the distributed operating system, reflecting the tool's philosophy of isolated, composable workspaces.

## Core Philosophy

**Projects as Isolated Contexts**
A project is the context in which an agent or human operates. Everything important to that context lives in the project directory. We deliberately copy resources into each project rather than symlinking or referencing external locations.

**Deliberate Copying, Not Sharing**
- Same source repository can exist in multiple projects as separate checkouts
- Documents (including Google Docs) are copied locally into project folders
- This enables independent evolution and later merging of state
- Encourages explicit context boundaries

**Usage-Based Exploration**
We are not doing large design up front. Features emerge from usage patterns and real needs.

## Decision

Build `cl9` as a Python CLI tool with the following initial design:

### Core Commands (MVP)
- `cl9 init` - Register/initialize a cl9 project in current directory
- `cl9 enter <project>` - Enter a project context and launch LLM session

### State Management

**Global State** (XDG standard directories)
- Owned and managed by cl9 tool
- Contains cross-project facilitation logic:
  - Project registry
  - Global configuration
  - Session history/metadata

**Project-Local State** (`.cl9/` directory)
- Lives in each project directory
- Contains project-specific configuration
- Session data and agent environments
- Multiple agents can exist within same project

**Project Files** (unopinionated)
- Projects can live anywhere on filesystem
- User controls project organization
- cl9 only manages `.cl9/` subdirectory

### Technology Choices
- **Language**: Python (ubiquitous, good CLI libraries, rapid iteration)
- **Distribution**: GitHub repository, installable via `uv tool install`
- **CLI Framework**: Click or Typer (decision deferred)
- **Package Structure**: Standard Python package with pyproject.toml

### Installation Method
```bash
# Local development install
uv tool install ~/p-workbench/src/cl9

# From GitHub (future)
uv tool install git+https://github.com/username/cl9
```

### Repository Layout
```
cl9/
├── docs/
│   └── adr/              # Architecture Decision Records
├── src/
│   └── cl9/              # Python package
│       ├── __init__.py
│       ├── cli.py        # CLI entry point
│       └── config.py     # Configuration management
├── pyproject.toml        # Package metadata
└── README.md             # Man-page style documentation
```

## Consequences

### Positive
- Short command name (`cl9`) is fast to type
- Isolated project contexts prevent cross-contamination
- Copying resources makes projects self-contained and portable
- XDG directories follow Unix conventions
- Python enables rapid prototyping and iteration
- UV tool install provides clean, isolated installation

### Negative
- Requires Python runtime
- Disk space overhead from copying resources
- User must manage synchronization between project copies

### Trade-offs Accepted
- Disk space vs. context clarity: We choose clarity
- Simplicity vs. efficiency: We choose simplicity
- Explicit vs. automatic: We choose explicit

## Alternatives Considered

1. **Symlink-based project organization**: Rejected - breaks context isolation
2. **Shell script**: Too limited for future expansion
3. **Go binary**: Overkill for MVP, slower iteration
4. **Workspace-based approach**: Rejected - too complex, less explicit

## Future Possibilities

The following are NOT planned, but possible directions based on usage:
- Project templates and scaffolding
- Session history search and replay
- Multi-agent coordination within projects
- Transcript analysis and summarization
- Integration with other tools (IDE, terminal multiplexers)
