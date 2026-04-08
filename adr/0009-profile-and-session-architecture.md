# ADR 0009: Profile and Session Architecture

**Date**: 2026-04-08

**Status**: Proposed

## Context

Three problems surfaced in close succession and turned out to be facets of the same design gap.

1. **Stale profiles after cl9 upgrades.** cl9 materialized profiles into `.cl9/profiles/<name>/` on first use. Upgrading cl9 had no effect on those copies, so projects quietly drifted from the shipped defaults with no visibility or upgrade path.

2. **Per-profile identity.** Users juggle multiple auth contexts — a personal Anthropic account, a work key routed through Bedrock, a client org's Vertex credentials. The current model reads auth from `~/.claude/` globally, so all sessions share one identity regardless of which cl9 profile they launched with.

3. **Broken resume across directories.** `cl9 agent continue` launches claude from the user's current working directory, but claude encodes the cwd into its session-storage path. Resuming from a different directory than the original spawn fails silently or starts a fresh session.

These are not three independent bugs. They are symptoms of cl9 having no clear separation between **profile source** (the template cl9 ships or the user curates), **session runtime** (the per-session materialized state claude actually reads), and **agent process** (the launched claude invocation and its working directory). Every fix has to pick a layer, and without a layered model, fixes collide.

This ADR defines that model.

## Decision

### Profile model

Profiles are read-only templates. A profile is a directory containing files like `CLAUDE.md`, `settings.json`, `mcp.json`, and a `manifest.json` declaring the agent executable (ADR 0008). Profiles exist at three scopes:

| Scope         | Location                  | Ownership                          |
|---------------|---------------------------|------------------------------------|
| Built-in      | cl9 package directory     | cl9 maintainers; immutable to users |
| User-global   | `~/.cl9/profiles/<name>/` | User                               |
| Project-local | `.cl9/profiles/<name>/`   | User; typically committed          |

Resolution order is project-local → user-global → built-in. First match wins.

Profiles are **never materialized** into project state. cl9 reads them in place, from wherever they resolve. Upgrading cl9 updates every built-in profile across every project instantly, because there are no copies to stale. Users who want to customize a built-in profile do so by creating a profile at a higher-priority scope — not by cloning and patching an existing one.

### Session runtime directory

Each session owns a dedicated runtime directory:

```
.cl9/sessions/<session-id>/runtime/
```

This directory is the **materialized session state** — a per-session snapshot derived from the profile plus whatever the init script writes. cl9 launches claude pointed at the files in this directory, *not* at the profile directory. The profile stays immutable; all per-session customization lands in the runtime.

The runtime directory is built once, at spawn time. It is never rebuilt on resume.

### Sealed sessions via `claude --bare`

cl9 launches every session with `claude --bare`. Under this flag, claude ignores settings, hooks, plugin sync, keychain reads, and `CLAUDE.md` auto-discovery from `~/.claude/`. Anthropic auth comes strictly from `ANTHROPIC_API_KEY` (exported in the process env or set via `settings.json`) or from an `apiKeyHelper` command in `settings.json`. All context — system prompt, settings, MCP configuration — must be passed explicitly as flags.

The launch invocation looks like:

```
claude --bare \
  --settings              <runtime>/settings.json \
  --mcp-config            <runtime>/mcp.json \
  --append-system-prompt-file <runtime>/CLAUDE.md
```

(`settings.local.json` is passed additionally when present.)

**Tolerated leak.** Even under `--bare`, claude writes session transcripts to `~/.claude/projects/<encoded-cwd>/*.jsonl`. That is the mechanism that makes `claude --resume` work, and cl9's resume path depends on it. The invariant cl9 cares about is one-directional: claude must not *read* auth or config from `~/.claude/`. History writes are fine.

### Spawn pipeline

`cl9 agent spawn` executes the following, in order:

1. Resolve the profile via the scope hierarchy above.
2. Create `.cl9/sessions/<id>/runtime/`.
3. Copy every regular file from the resolved profile directory into the runtime directory. This is the default baseline: if there is no init script, the runtime is exactly a copy of the profile.
4. If `.cl9/init/init.py` exists, run it (see contract below). The script mutates runtime files in place, typically using the `cl9.claude` helpers.
5. Launch `claude` with `--bare` and explicit config flags pointing into the runtime directory, with working directory set to the session's spawn working directory.

A non-zero exit from the init script aborts the spawn. No session state is persisted on abort.

### Project-local init script

A project may provide a single init script at `.cl9/init/init.py`. One file per project, Python only, no profile-based dispatch — if the user wants different behavior per profile, the script branches on `CL9_PROFILE_NAME` itself.

**Contract:**

- **Location.** Exactly `<CL9_PROJECT_ROOT>/.cl9/init/init.py`. No alternative extensions, no alternative locations, no profile-keyed filenames.
- **Execution.** `subprocess.run([sys.executable, str(init_path)], cwd=session_cwd, env=merged_env, check=True)`. The script is a normal Python file with a script body — no required function signature, no import protocol.
- **Interpreter.** cl9's own `sys.executable`. The script has access to cl9's standard library and cl9's installed packages, including the `cl9.claude` helper module. No separate Python runtime needs to be installed.
- **Working directory.** The session's spawn working directory — the same directory claude will run from.
- **Environment.** Inherits cl9's environment (which inherits the user's shell environment), plus:

| Variable            | Meaning                                                         |
|---------------------|-----------------------------------------------------------------|
| `CL9_PROJECT_ROOT`  | Absolute path to the project root (where `.cl9/` lives)         |
| `CL9_PROFILE_DIR`   | Absolute path to the resolved immutable profile source          |
| `CL9_PROFILE_NAME`  | Name of the resolved profile                                    |
| `CL9_RUNTIME_DIR`   | Absolute path to the per-session runtime directory (writable)   |
| `CL9_SESSION_ID`    | Session UUID                                                    |
| `CL9_SESSION_NAME`  | Session name, or empty string                                   |

- **Failure.** Non-zero exit aborts the spawn. Stderr is surfaced to the user; the runtime directory is cleaned up; no session is registered.

### Helper module: `cl9.claude`

To keep init scripts short and avoid reinventing JSON merging, cl9 ships a thin helper module:

```python
# cl9/claude.py

def merge_to_settings(patch: dict) -> None:
    """Deep-merge patch into <CL9_RUNTIME_DIR>/settings.json."""

def merge_to_settings_local(patch: dict) -> None:
    """Deep-merge patch into <CL9_RUNTIME_DIR>/settings.local.json."""

def merge_to_mcp(patch: dict) -> None:
    """Deep-merge patch into <CL9_RUNTIME_DIR>/mcp.json."""
```

**Merge semantics.** Nested dicts recurse. Scalars and lists replace. `None` is a normal value, not a delete sentinel. Files are created if they do not already exist.

**Context resolution.** The helpers read `CL9_RUNTIME_DIR` from the process environment. They take no arguments beyond the patch and carry no context object. An init script that calls them outside of a cl9 spawn (e.g., during local testing) must set `CL9_RUNTIME_DIR` in its own environment.

That is the entire initial surface. cl9 deliberately ships nothing for identity providers — no `from_op()`, no `from_keyring()`, no vault glue. Init scripts that need secrets shell out to the relevant CLI (`op`, `aws`, `gcloud`, `gopass`) via `subprocess`. Keeping cl9 out of the identity space is a load-bearing choice, not an oversight.

Example `init.py`:

```python
import subprocess
from cl9.claude import merge_to_settings

api_key = subprocess.check_output(
    ["op", "read", "op://Work/anthropic/api-key"],
    text=True,
).strip()

merge_to_settings({
    "env": {
        "ANTHROPIC_API_KEY": api_key,
    },
})
```

### Project scaffolding

`cl9 init` writes `.cl9/init/init-example.py` as part of project scaffolding. The file contains a minimal script body and commented-out examples (1Password, AWS profile switching, MCP token injection). cl9 does **not** create `init.py` directly. The presence of `init.py` is the signal that the user has explicitly opted in; until then, cl9 falls back to the no-op copy (step 3 of the spawn pipeline) and the example file sits next to it for reference.

### Resume behavior

`cl9 agent continue` always launches claude from the session's **spawn working directory**, which is stored in session state on first spawn. It is *not* launched from the user's current directory.

This exists because claude encodes the cwd into its session-storage path (`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`). Invoking claude from a different directory than the original spawn causes it to look in the wrong place and either fail to resume or start a fresh session — the failure mode that surfaced this whole design discussion.

**Bake-once init.** Resume does not re-run `init.py`. The runtime directory from the original spawn is reused untouched. The consequence is that short-lived credentials baked into `settings.json` at spawn time may expire between spawn and resume. When that happens, the user's recovery is to fork the session (which triggers a fresh spawn with a fresh init run) or manually delete the runtime directory. This is tolerated in the initial design.

## Consequences

### Positive

- Immutable profiles eliminate the entire stale-after-upgrade class of problems. Upgrading cl9 upgrades every project at once.
- Per-project identity works cleanly: projects with different auth needs have different `.cl9/init/init.py` files.
- Sealed sessions sever the implicit dependency on global `~/.claude/` state, so sessions are reproducible from profile + init alone.
- The three-layer model (profile source → session runtime → agent process) is explicit and named, which makes future features easier to place.
- cl9 stays identity-agnostic. Credential handling is entirely in user-owned scripts calling user-chosen tools.
- Init scripts use cl9's own Python interpreter, so there is no separate runtime to ship, install, or manage.

### Negative

- Per-profile identity within a single project requires the init script to dispatch on `CL9_PROFILE_NAME`. Users who want strict per-profile auth without a conditional must split into separate projects.
- Token staleness on resume is tolerated. Credentials that expire between spawn and resume break the session, and recovery requires a user action (fork or delete-and-respawn).
- Users own `init.py` and any bugs it contains. cl9 can do little beyond surfacing stderr when a script misbehaves.
- The initial helper surface is intentionally narrow. Users will ask for more (env propagation for non-Anthropic vars, provider sugar), and the answer for v1 is no.

## Non-goals

The following are explicitly out of scope for this ADR, so a reader knows where the line is:

- **Session discovery UX.** Interactive TUI selectors, fuzzy search, transcript preview, enhanced `session list` formatting. These are convenience features that can ship without architectural decisions.
- **Profile management commands.** `profile clone`, `profile diff`, `profile audit`, `profile list`, `profile install-mcp`. Users who need custom profiles create files directly at user-global or project-local scope.
- **MCP installation command.** MCP servers are declared in a profile's `mcp.json`. Init scripts mutate that configuration in the runtime dir when needed; persistent additions require authoring a new profile. There is no `cl9 install-mcp`.
- **Non-Anthropic env var propagation.** The helper surface only touches `settings.json`, `settings.local.json`, and `mcp.json`. Injecting variables claude itself doesn't read (e.g., `AWS_PROFILE`, `GOOGLE_APPLICATION_CREDENTIALS`) requires setting them in the parent shell before `cl9 agent spawn`. A future ADR can add a runtime env-injection mechanism if the pain justifies it.
- **Project relocatability.** Moving a project to a different path invalidates existing session runtimes and claude's session storage. Clear errors on failure are enough for now.
- **Third-party init dependencies.** Init scripts get cl9's venv — stdlib plus whatever cl9 itself depends on. Scripts that want richer functionality should shell out to CLIs rather than `pip install` into cl9's environment. PEP 723 inline script dependencies via `uv run --script` are a plausible future direction; not v1.

## Relationship to other ADRs

- **Extends ADR 0008** (profile-bound agent executables): profiles still declare the agent binary via `manifest.json`; the runtime directory is what the binary actually reads at launch time.
- **Consolidates three prior drafts.** Earlier proposals on immutable profiles, session discovery and resume, and sealed sessions with init scripts are superseded in full by this ADR. The resume-working-directory fix and the immutable profile model are preserved; the session-discovery UX is reclassified as a non-architectural convenience and dropped from the ADR trail.

## References

- `claude --help` — `--bare` flag semantics and auth behavior
- ADR 0008 — Profile-Bound Agent Executables
- GitHub Issue #2 — Stale agent profiles after cl9 upgrade
- GitHub Issue #3 — Poor session overview and resume UX
