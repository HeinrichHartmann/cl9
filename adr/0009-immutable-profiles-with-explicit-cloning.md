# ADR 0009: Immutable Profiles with Explicit Cloning

**Date**: 2026-04-07

**Status**: Proposed

## Context

ADR 0008 introduced profile-bound agent executables where profiles select the agent runtime. The current implementation automatically materializes profiles into `.cl9/profiles/<name>/` on first use, creating a project-local working copy.

This creates a critical problem: **stale profiles after cl9 upgrades**.

When cl9 is upgraded with improved built-in profiles (better prompts, new settings, bug fixes), existing projects continue using their materialized copies. Users have no visibility into profile updates and no clean upgrade path.

### Current Behavior Issues

1. **Materialization happens silently** - users don't know they're getting a copy
2. **Updates don't propagate** - upgrading cl9 doesn't update project profiles
3. **No versioning** - no way to know if profiles are outdated
4. **Heavy-handed workaround** - `cl9 init --force` clobbers all project files

### Investigation Results

Through code analysis (see GitHub issue #2), we determined:

- **Claude Code never modifies profile directories** - it reads config files via CLI flags (`--append-system-prompt-file`, `--settings`, `--mcp-config`)
- **Claude Code writes to `~/.claude/`** - it uses `--setting-sources user` which writes to global config
- **Only cl9 writes to `.cl9/profiles/`** - during init and first spawn

This means **profile directories can be immutable** without breaking Claude Code.

### Industry Patterns

Research into similar tools reveals consistent patterns:

1. **Kubernetes Kustomize** ([docs](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/))
   - Immutable **base** configurations shipped with the tool
   - User-specific **overlays** patch on top
   - Base updates automatically when tool updates
   - Clear separation of "what Kubernetes provides" vs "what user changed"

2. **Harness CI/CD** ([docs](https://developer.harness.io/docs/platform/templates/template/))
   - Git-based pipeline templates with versioning
   - Templates at different scopes (account/org/project)
   - Stable versions propagate changes to all pipelines
   - Users can fork templates for customization

3. **Agent Frameworks** (Microsoft Agent Framework, OpenAI Agents SDK) ([docs](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/))
   - Declarative YAML configurations loaded at runtime
   - No materialization - read directly from source
   - Swap configurations by changing references

4. **Tmux Session Managers** ([sesh](https://github.com/joshmedeski/sesh))
   - Simple config files read from standard locations
   - No copying - users create their own configs as separate files

**Common themes:**
- Source configurations remain immutable
- Tool updates = automatic config updates
- Customization requires explicit fork/overlay/new-name
- Clear ownership model

## Decision

### Built-in Profiles Are Read-Only Sources

cl9 ships built-in profiles in the package directory. These are **never materialized** to `.cl9/profiles/`.

Profile resolution order becomes:

1. `.cl9/profiles/<name>/` - **user-cloned profiles only**
2. `~/.cl9/profiles/<name>/` - user-installed global profiles
3. Built-in profiles from cl9 package - **always current with cl9 version**

When spawning an agent with the built-in `default` profile, cl9 reads directly from:
```
<cl9_package>/profiles/default/CLAUDE.md
<cl9_package>/profiles/default/settings.json
<cl9_package>/profiles/default/manifest.json
```

No `.cl9/profiles/default/` directory is created unless the user explicitly requests it.

### Transparent Updates

Upgrading cl9 immediately updates all built-in profiles for all projects.

- `pip install --upgrade cl9` → new built-in profiles available immediately
- No per-project migration needed
- No stale state

This matches how users expect package upgrades to work.

### Explicit Cloning for Customization

When users want to customize a profile, they **explicitly clone** it:

```bash
cl9 profile clone default my-custom
```

This:
1. Creates `.cl9/profiles/my-custom/`
2. Copies all files from the source profile
3. Marks it as user-owned (future: could track source + version)
4. Allows modification

Cloned profiles are **user-owned mutable state** and never auto-updated.

Users can also install profiles globally:

```bash
cl9 profile install ~/my-profiles/codex-python ~/.cl9/profiles/codex-python
```

### Profile Scope Hierarchy

Profiles exist at three scopes:

| Scope | Location | Ownership | Updates |
|-------|----------|-----------|---------|
| **Built-in** | `<cl9_package>/profiles/` | cl9 maintainers | Via `pip install --upgrade cl9` |
| **User global** | `~/.cl9/profiles/` | User | Manual (user edits or reinstalls) |
| **Project-local** | `.cl9/profiles/` | User | Manual (user edits) |

Resolution order: project-local → user global → built-in.

This matches the scope hierarchy users expect from tools like Git, npm, etc.

### No Automatic Materialization

The `_materialize_profile()` function in `cli.py:230-248` is **removed**.

`cl9 agent spawn` never creates `.cl9/profiles/<name>/`. It resolves the profile and passes absolute paths to the adapter.

Adapters receive the resolved profile path and construct commands like:
```bash
claude --append-system-prompt-file /path/to/cl9/profiles/default/CLAUDE.md \
       --settings /path/to/cl9/profiles/default/settings.json
```

### Session Compatibility

Sessions track which profile name they use (already implemented in ADR 0005/0008).

If a user:
1. Creates session with `default` (built-in)
2. Later clones to `.cl9/profiles/default/` (project-local)
3. Continues the session

The session will now use the project-local profile (because of resolution order).

This is **correct behavior** - the user explicitly chose to override the built-in.

If this is undesirable, the user should clone to a new name: `cl9 profile clone default my-default`.

### MCP Installation Special Case

MCP servers are installed into profile directories:
```bash
cl9 profile install-mcp default @modelcontextprotocol/server-filesystem
```

This must still work. When installing MCP servers:

1. Check if `.cl9/profiles/default/` exists
2. If not, prompt: "Profile 'default' is built-in. Clone to project? [y/N]"
3. If yes, run implicit `cl9 profile clone default default`
4. Install MCP server into `.cl9/profiles/default/mcp.json`

Users learn that **MCP installation requires a mutable profile copy**.

Alternative: MCP servers could be installed globally to `~/.cl9/profiles/default/mcp.json`, but that's project-specific tooling, so project-local is more appropriate.

## Consequences

### Positive

- **No stale profiles** - upgrading cl9 immediately upgrades all built-in profiles
- **Transparent updates** - users get bug fixes and improvements automatically
- **Clear ownership** - built-in vs user-owned is obvious from location
- **Matches user expectations** - works like other package-based tools
- **No version tracking needed** - built-in profiles implicitly versioned by cl9 version
- **Simpler implementation** - remove materialization logic

### Negative

- **Breaking change** - existing `.cl9/profiles/default/` directories become user-owned overrides
- **MCP installation requires extra step** - must clone profile first
- **Users who modified `.cl9/profiles/default/` unknowingly** will be surprised when they no longer get updates

### Migration Strategy

For the breaking change, provide a migration command:

```bash
cl9 profile audit
```

This checks for project-local profiles that match built-in names and reports:

```
⚠️  Found project-local override of built-in profile: .cl9/profiles/default/

This profile will take precedence over cl9's built-in 'default' profile.

If you customized this profile, keep it.
If you didn't customize it, delete it to use cl9's built-in version:
  rm -rf .cl9/profiles/default/

To compare: cl9 profile diff default
```

Include this in release notes with clear upgrade instructions.

### Trade-offs Accepted

- Project-local profiles can shadow built-ins (but this is expected from resolution order)
- MCP installation requires explicit clone step (acceptable - makes ownership clear)
- Migration burden for existing projects (one-time, well-documented)

## Implementation Steps

1. Remove `_materialize_profile()` function from `cli.py`
2. Update `_resolve_agent_profile()` to work with non-materialized profiles
3. Add `cl9 profile clone <source> <dest>` command
4. Add `cl9 profile audit` command for migration help
5. Add `cl9 profile list` command showing scope (built-in/global/project)
6. Add `cl9 profile diff <name>` to compare project-local vs built-in
7. Update MCP installation to prompt for cloning
8. Update documentation and migration guide
9. Add tests for resolution order
10. Update ADR 0006 to clarify profile ownership model

## Relationship to Other ADRs

- **Supersedes parts of ADR 0006**: Project-local profiles are no longer "mutable runtime state" by default - they're explicitly cloned user state
- **Refines ADR 0008**: Profiles still bind to agent executables, but source location changes
- **Compatible with ADR 0005**: Session tracking unchanged - sessions reference profile names

## References

- GitHub Issue #2: Stale agent profiles after cl9 upgrade
- [Kubernetes Kustomize: Base and Overlay patterns](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)
- [Harness CI/CD: Template versioning and management](https://developer.harness.io/docs/platform/templates/template/)
- [Microsoft Agent Framework: Declarative agent configuration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/)
- [Kustomize guide with examples](https://devopscube.com/kustomize-tutorial/)
- [Harness template best practices](https://developer.harness.io/docs/platform/templates/templates-best-practices/)
