# ADR 0008: Profile-Bound Agent Executables

**Date**: 2026-04-05

**Status**: Accepted

## Context

cl9 already has the concept of an agent profile, but the current model still assumes Claude-style launch semantics in too many places:

- one hardcoded executable
- one hardcoded prompt/config file layout
- one hardcoded session model where cl9 can inject the tool session ID up front

That does not hold once profiles are used to select different agent runtimes such as Claude Code, Codex, or future tools.

The core problem is broader than choosing a different binary name:

1. Different tools expose different launch commands.
2. Different tools use different configuration surfaces.
3. Different tools expose session identity differently.
4. Some tools let cl9 set the runtime session ID at spawn time; others do not.

We want profiles to be the place where a session's agent runtime is chosen, while keeping session management and project-local state inside cl9.

This ADR refines ADR 0005 and ADR 0006.

## Decision

### Profiles Select the Agent Runtime

A profile does not only provide prompt/config files. It also selects which agent runtime is launched.

Examples:

- `default` -> Claude Code
- `codex` -> Codex CLI
- future profile -> Copilot CLI

The runtime choice is part of the profile definition, not a separate top-level CLI flag.

### Profiles Bind to Launch Adapters

Each profile is interpreted through a tool-specific launch adapter.

The adapter owns:

- how to invoke the executable
- how to pass profile-local instructions
- how to pass tool-specific config
- how `spawn`, `continue`, and `fork` map to the tool's CLI
- how tool-native session IDs are created, injected, or discovered

This means cl9 should not assume that all tools behave like Claude Code just because they are launched through `cl9 agent ...`.

### Executables Come From the Project Environment

cl9 does not manage global installation of Claude, Codex, or other agent CLIs.

The executable is resolved from the effective project environment, including:

- the normal shell environment
- project-local `bin/`
- template-provided tools
- surrounding environment setup such as direnv or Nix

This matches ADR 0007: agent tooling is part of the project's capability bundle.

### Session Identity Has Two Layers

cl9 keeps its own project-local session identity regardless of which tool is used.

There are therefore two distinct IDs:

- `cl9_session_id`: cl9's durable project-local session identifier
- `tool_session_id`: the runtime tool's native session identifier, if the tool exposes one

For tools like Claude Code, these may be the same because cl9 can inject the session ID at launch.

For tools like Codex, they may differ because the runtime session ID may only be known after launch or after process exit.

ADR 0005's earlier assumption that cl9 always generates and injects the runtime session ID is therefore narrowed:

- it remains true for tools that support injected session identity
- it is not a universal invariant across all agent runtimes

### Continue and Fork Target Tool Sessions Through cl9 State

`cl9 agent continue` and `cl9 agent fork` continue to resolve sessions through project-local cl9 state.

But the launch adapter decides how that maps to the runtime:

- if the tool uses injected IDs, cl9 can continue/fork directly
- if the tool needs its own native session ID, cl9 must persist that mapping
- if cl9 does not yet know the native session ID for a session, continue/fork must fail with a clear error rather than guessing

### Tool-Specific Profile Files Are Allowed

Not every profile uses the same files.

Examples:

- Claude profile:
  - `CLAUDE.md`
  - `settings.json`
  - `mcp.json`
- Codex profile:
  - instructions file for Codex
  - optional Codex config fragment
- future tools:
  - tool-specific config files as needed

Profile contents are therefore tool-specific assets interpreted by the selected adapter, not a universal schema beyond a small cl9-owned manifest.

### Project-Local Working Copies Stay the Same

Source profiles still come from:

1. `.cl9/profiles/<name>/` if already materialized
2. `~/.cl9/profiles/<name>/`
3. built-in shipped profiles

When a profile is used, cl9 materializes a project-local working copy in:

```text
.cl9/profiles/<name>/
```

That working copy is managed runtime state and may be mutated by cl9 or by the running agent.

## Minimal Profile Manifest

Profiles should include a small cl9-owned manifest that declares at least:

```json
{
  "tool": "claude",
  "executable": "claude"
}
```

Possible examples:

```json
{
  "tool": "codex",
  "executable": "codex"
}
```

This manifest identifies which launch adapter to use and which executable name cl9 should resolve in the project environment.

The rest of the profile directory remains tool-specific.

## Consequences

### Positive

- profiles become the single place where agent runtime is selected
- cl9 can support multiple agent CLIs without a separate top-level tool switch
- Claude-specific assumptions stop leaking into all agent flows
- session tracking remains project-local even when tool-native session models differ
- future tools can be added without redesigning the public CLI

### Negative

- ADR 0005's original session model becomes more complex
- session tracking must handle cases where tool-native IDs are discovered later
- profile validation becomes slightly more involved
- not every tool will support the same level of session fidelity on day one

## Trade-offs Accepted

- cl9 owns the project-local session model, even if a tool has weaker or different native semantics
- profiles choose the runtime, instead of adding a separate `--tool` switch
- tool-specific config formats are accepted as a normal part of the design
- exact feature parity across all runtimes is not required immediately

## Next Implementation Implications

1. Add a small manifest to each shipped profile declaring `tool` and `executable`.
2. Refactor agent launch code around tool adapters instead of Claude-specific branching.
3. Extend project-local session state to store `tool_session_id` separately from cl9's own session ID when needed.
4. Make `continue` and `fork` fail clearly when a required tool-native session mapping is not yet known.
5. Keep all session tracking project-local; do not mutate `~/.claude/`, `~/.codex/`, or other global tool state in this iteration.
