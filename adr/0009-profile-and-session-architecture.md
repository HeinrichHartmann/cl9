# ADR 0009: Profile and Session Architecture

**Date**: 2026-04-08

**Status**: Proposed

## Context

Three problems surfaced in close succession and turned out to be facets of the same design gap.

1. **Stale profiles after cl9 upgrades.** cl9 materialized profiles into `.cl9/profiles/<name>/` on first use. Upgrading cl9 had no effect on those copies, so projects quietly drifted from the shipped defaults with no visibility or upgrade path.

2. **Per-profile identity.** Users juggle multiple auth contexts — a personal Anthropic account, a work key routed through Bedrock, a client org's Vertex credentials. The current model reads auth from `~/.claude/` globally, so all sessions share one identity regardless of which cl9 profile they launched with.

3. **Broken resume across directories.** `cl9 agent continue` launches claude from the user's current working directory, but claude encodes the cwd into its session-storage path. Resuming from a different directory than the original spawn fails silently or starts a fresh session.

These are not three independent bugs. They are symptoms of cl9 having no clear separation between **profile source** (the template cl9 ships), **session runtime** (the per-session materialized state claude actually reads), and **agent process** (the launched claude invocation and its working directory). Every fix has to pick a layer, and without a layered model, fixes collide.

This ADR defines that model.

## Decision

### Profile model

Profiles are read-only templates that ship with cl9. A profile is a directory containing files like `CLAUDE.md`, `settings.json`, `mcp.json`, and a `manifest.json` declaring the agent executable (ADR 0008). Users do not author profiles; how developers might ship profile packages is deferred to a future ADR.

The model is one equation:

    profile (immutable base, from cl9) + init.py (thin overlay) → runtime (per-session, materialized)

The profile owns the bulk of the agent's configuration. `init.py` is a deliberately thin mutation layer for **orthogonal concerns** — auth, MCP token injection, project-specific overrides. Both are projected onto a per-session runtime directory and passed to claude via explicit flags. The runtime is never merged with user-global `~/.claude/` state.

Because profiles are not copied into project state, upgrading cl9 takes effect on the next session spawn — no migration, no stale copies.

### Session runtime directory

Each session owns a dedicated runtime directory:

```
.cl9/sessions/<session-id>/runtime/
```

This is the **materialized session state** — a per-session snapshot derived from the profile plus whatever the init script wrote. cl9 launches claude pointed at the files in this directory, *not* at the profile directory. The profile stays immutable; all per-session customization lands in the runtime.

The runtime directory is built once, at spawn time. It is never rebuilt on resume.

### Sealed sessions: claude adapter

cl9 launches claude with `claude --bare`. Under this flag, claude ignores settings, hooks, plugin sync, keychain reads, and `CLAUDE.md` auto-discovery from `~/.claude/`. Anthropic auth comes strictly from `ANTHROPIC_API_KEY` in the process env or from an `apiKeyHelper` command in `settings.json`. All context — system prompt, settings, MCP configuration — must be passed explicitly as flags.

The launch invocation looks like:

```
claude --bare \
  --session-id                <cl9-session-id> \
  --settings                  <runtime>/settings.json \
  --mcp-config                <runtime>/mcp.json \
  --append-system-prompt-file <runtime>/CLAUDE.md
```

`cl9 agent continue` launches the identical invocation against the same runtime directory, with `--resume <session-id>` appended, from the captured spawn cwd. `cl9 agent fork` runs the full spawn pipeline (fresh runtime, fresh init) and adds claude's `--fork-session` flag.

**Per-adapter sealing.** The `--bare` model is claude-specific. Each launch adapter (ADR 0008) owns its own mapping from `cl9.agent` state and runtime-dir files to its CLI flags, and its own sealing story. Adapters for tools without an equivalent to `--bare` have weaker isolation guarantees; that is a property of the tool, not of cl9.

**Tolerated leak.** Even under `--bare`, claude writes session transcripts to `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. That is the mechanism that makes `claude --resume` work, and cl9's resume path depends on it. The invariant cl9 cares about is one-directional: claude must not *read* auth or config from `~/.claude/`. History writes are fine.

We recommend keeping all configuration in the project (via profile + `init.py`) and leaving `~/.claude/` free of manual edits.

### The `cl9.agent` module

cl9 owns the agent configuration model. It exposes that model to init scripts as the `cl9.agent` module:

```python
# cl9/agent.py — populated by cl9 at spawn time, mutated by init.py

# --- mutable agent configuration ---
env: dict[str, str] = {}     # forwarded into the agent process env at launch
settings: dict = {}          # serialized to <runtime>/settings.json
mcp: dict = {}               # serialized to <runtime>/mcp.json

# --- read-only session context ---
project_root: Path           # absolute path to the project root
profile_name: str            # name of the resolved profile
profile_dir: Path            # absolute path to the immutable profile source
runtime_dir: Path            # absolute path to this session's runtime directory
session_id: str              # session UUID
session_name: str | None     # session name, or None
```

That is the full surface. Three mutable dicts, six read-only attributes, no helper functions.

cl9 initializes the mutable dicts before init.py runs:

- `env` starts empty.
- `settings` is loaded from the profile's `settings.json` if present, else `{}`.
- `mcp` is loaded from the profile's `mcp.json` if present, else `{}`.

`init.py` mutates these dicts in place. After init returns, cl9 serializes `agent.settings` → `<runtime>/settings.json` and `agent.mcp` → `<runtime>/mcp.json`, then launches the agent with a process env composed of `os.environ ⊕ agent.env ⊕ {CL9_* context vars}`.

In the typical case, init.py only mutates `env` (auth). `settings` and `mcp` are escape hatches for less common cases (`apiKeyHelper`, hooks, runtime MCP token injection). The layer stays thin: cl9 owns the bulk of the config via the profile, and init.py contributes orthogonal slices.

Example `init.py`:

```python
import subprocess
from cl9 import agent

api_key = subprocess.check_output(
    ["op", "read", "op://Work/anthropic/api-key"],
    text=True,
).strip()

agent.env["ANTHROPIC_API_KEY"] = api_key
```

Per-profile auth via branching:

```python
import subprocess
from cl9 import agent

if agent.profile_name == "work":
    secret_ref = "op://Work/anthropic/api-key"
elif agent.profile_name == "personal":
    secret_ref = "op://Personal/anthropic/api-key"
else:
    secret_ref = None

if secret_ref:
    agent.env["ANTHROPIC_API_KEY"] = subprocess.check_output(
        ["op", "read", secret_ref], text=True,
    ).strip()
```

cl9 deliberately ships nothing for identity providers — no `from_op()`, no `from_keyring()`, no vault glue. Init scripts that need secrets shell out to the relevant CLI (`op`, `aws`, `gcloud`, `gopass`) via `subprocess`. Keeping cl9 out of the identity space is a load-bearing choice, not an oversight.

### Spawn pipeline

`cl9 agent spawn` executes the following, in order:

0. **Plugin hooks.** Run `pre_agent` and `on_agent` plugin hooks. If a hook claims the spawn (returns truthy from `on_agent`), the rest of the pipeline is skipped.

1. **Resolve profile.** Look up the requested profile in cl9's built-in set.

2. **Create runtime directory.** `mkdir -p .cl9/sessions/<id>/runtime/`.

3. **Copy non-config files.** Raw-copy every regular file from the profile directory into the runtime directory, preserving mode bits. **Skip `manifest.json`, `settings.json`, and `mcp.json`** — cl9 reads those directly into `cl9.agent` state in step 4 and writes them in step 6. No template rendering: files are copied byte-for-byte. Per-session variability lives in `cl9.agent` and in claude's own env-var expansion in command strings (e.g., `${CL9_RUNTIME_DIR}` inside a `statusLine.command`).

4. **Initialize `cl9.agent` state.** Set the read-only context attributes (`project_root`, `profile_name`, `profile_dir`, `runtime_dir`, `session_id`, `session_name`). Set `agent.env = {}`. Load the profile's `settings.json` into `agent.settings` (or `{}` if absent). Load the profile's `mcp.json` into `agent.mcp` (or `{}` if absent).

5. **Run `init.py` if present.** If `<project_root>/.cl9/init/init.py` exists, run it via `runpy.run_path(init_path, run_name="__cl9_init__")` in cl9's own Python process. cl9 changes its working directory to the session's spawn cwd before running init, and restores it afterward. The script mutates `cl9.agent` directly. Any uncaught exception or non-zero `SystemExit` aborts the spawn: cl9 surfaces the traceback, deletes the runtime directory, and writes no session row.

6. **Serialize `cl9.agent` config.** Write `agent.settings` to `<runtime>/settings.json` and `agent.mcp` to `<runtime>/mcp.json` (each pretty-printed JSON; either is omitted if the corresponding dict is empty).

7. **Write session row.** Insert into `.cl9/state.db`, capturing the spawn cwd.

8. **Launch.** Build the agent process env (see below), then launch the adapter command from the spawn cwd.

`cl9 agent fork` runs the full pipeline against a new session ID, producing a fresh runtime and a fresh init run. This is the documented recovery path for stale credentials baked into a previous session's runtime.

**Runtime-dir lifecycle invariant.** A session's runtime directory and its DB row are created and destroyed together. `cl9 session delete` and `cl9 session prune` remove both. An aborted init run deletes the runtime directory before returning, and no DB row is written.

### Project-local init script

A project may provide a single init script at `<project_root>/.cl9/init/init.py`. One file per project, Python only, no profile-based dispatch — if the user wants different behavior per profile, the script branches on `cl9.agent.profile_name` itself.

**Contract:**

- **Location.** Exactly `<project_root>/.cl9/init/init.py`. No alternative extensions, no alternative locations, no profile-keyed filenames.
- **Execution.** `runpy.run_path(init_path, run_name="__cl9_init__")` in cl9's own Python process. The script is a normal Python file with a script body — no required function signature, no import protocol.
- **Interpreter and dependencies.** cl9's own Python process. The script has access to cl9's standard library, cl9's installed packages, and the `cl9.agent` module. No separate Python runtime needs to be installed. Scripts that want richer dependencies should shell out to CLIs.
- **Working directory.** cl9 sets `os.getcwd()` to the session's spawn working directory before running the script and restores its previous cwd afterward.
- **Context.** Read via `cl9.agent` attributes: `project_root`, `profile_name`, `profile_dir`, `runtime_dir`, `session_id`, `session_name`.
- **Failure.** Any uncaught exception or non-zero `SystemExit` aborts the spawn. The traceback is surfaced; the runtime directory is removed; no session is registered.

### Agent process environment

cl9 launches the agent (spawn step 8) with a process environment composed of:

1. cl9's own `os.environ` (which inherits the user's shell environment)
2. plus everything in `cl9.agent.env` (mutated by init.py)
3. plus the following `CL9_*` context variables, so command strings in `settings.json` (status lines, hooks) can reference them:

| Variable            | Meaning                                                         |
|---------------------|-----------------------------------------------------------------|
| `CL9_PROJECT_ROOT`  | Absolute path to the project root                               |
| `CL9_PROFILE_NAME`  | Name of the resolved profile                                    |
| `CL9_RUNTIME_DIR`   | Absolute path to this session's runtime directory               |
| `CL9_SESSION_ID`    | Session UUID                                                    |
| `CL9_SESSION_NAME`  | Session name, or empty string                                   |

`CL9_PROFILE_DIR` is exposed to init.py (via `cl9.agent.profile_dir`) but **not** to the launched agent — the agent has no business reading from cl9's package tree, and that path is coupled to the cl9 version.

The shipped default `settings.json` references `${CL9_RUNTIME_DIR}/statusline.py` in its `statusLine.command`, relying on claude's own env-var expansion in command strings.

### Project scaffolding

`cl9 init` writes:

- `.cl9/config.json` — project metadata
- `.cl9/init/init-example.py` — a commented example with idioms for 1Password, AWS profile switching, and MCP token injection
- environment template files (e.g., `flake.nix`, `MEMORY.md`, `README.md`) per the selected environment type

It does **not** create `.cl9/profiles/` — profiles ship with cl9. It does **not** create `.cl9/init/init.py`; the presence of that file is the user's explicit opt-in signal. Until the user copies or renames `init-example.py` to `init.py`, the spawn pipeline skips step 5 entirely and the runtime is exactly the profile baseline.

`cl9 init --force` re-applies environment templates and refreshes `init-example.py`, but **never overwrites an existing `init.py`**.

### Resume behavior

`cl9 agent continue` always launches claude from the session's **spawn working directory**, which is stored in session state on first spawn. It is *not* launched from the user's current directory.

This exists because claude encodes the cwd into its session-storage path (`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`). Invoking claude from a different directory than the original spawn causes it to look in the wrong place and either fail to resume or start a fresh session — the failure mode that surfaced this whole design discussion.

**Resume guardrail.** Before launching, cl9 checks that the expected jsonl file exists at the encoded path. If missing, cl9 aborts with an actionable error rather than letting claude silently start a fresh session. The two common causes are (a) the project directory was moved after spawn, and (b) `~/.claude/projects/` was manually pruned.

**Bake-once init.** Resume does not re-run `init.py`. The runtime directory from the original spawn is reused untouched. The consequence is that short-lived credentials baked into `agent.env` or `settings.json` at spawn time may expire between spawn and resume. When that happens, the user's recovery is to fork the session (which runs the full spawn pipeline with a fresh init). This is tolerated in the initial design.

## Consequences

### Positive

- Immutable profiles eliminate the entire stale-after-upgrade class of problems. Upgrading cl9 upgrades every project at once.
- Per-project identity works cleanly: projects with different auth needs have different `.cl9/init/init.py` files. Per-profile-within-project identity works via branching on `cl9.agent.profile_name`.
- Sealed sessions sever the implicit dependency on global `~/.claude/` state, so sessions are reproducible from profile + init alone (modulo the tolerated transcript leak).
- The three-layer model (profile source → session runtime → agent process) is explicit and named, which makes future features easier to place.
- `cl9.agent.env` gives init.py direct control over the launched agent's process environment. Variables that claude itself doesn't read but that MCP servers and tool subprocesses do (`AWS_PROFILE`, `GOOGLE_APPLICATION_CREDENTIALS`, etc.) just work via the same surface.
- cl9 stays identity-agnostic. Credential handling is entirely in user-owned scripts calling user-chosen tools.
- Init scripts run in cl9's own Python process: no separate runtime to ship, install, or manage; cl9's stdlib and packages are available for free; tracebacks are native.
- The init surface is small (three dicts, six attributes) and the model is "cl9 owns the agent config; init.py is a thin overlay." Going fully declarative would require init.py to own the *entire* config, creating a second source of truth and a maintenance burden cl9 deliberately avoids.

### Negative

- Per-profile identity within a single project requires the init script to dispatch on `cl9.agent.profile_name`. Users who want strict per-profile auth without a conditional must split into separate projects.
- Token staleness on resume is tolerated. Credentials that expire between spawn and resume break the session, and recovery requires a user action (fork or delete-and-respawn).
- Users own `init.py` and any bugs it contains. cl9 surfaces the traceback when a script raises but cannot do much beyond that.
- Init scripts run in cl9's process. cl9 resets `cl9.agent` state and restores cwd around each run, and cl9 itself exits after the agent process exits, so any state pollution is bounded by the lifetime of a single `cl9 agent spawn` invocation. Within that bound, init.py is trusted code by definition.
- The `cl9.agent` surface is intentionally narrow. Users will ask for typed schemas, MCP server helpers, secret-store integrations — and the answer for v1 is no.

## Non-goals

The following are explicitly out of scope for this ADR, so a reader knows where the line is:

- **Session discovery UX.** Interactive TUI selectors, fuzzy search, transcript preview, enhanced `session list` formatting. These are convenience features that can ship without architectural decisions.
- **User-authored profiles.** Profiles ship with cl9. Users customize runtime behavior via `init.py`, not by authoring new profiles. How developers might distribute profile packages is deferred to a future ADR.
- **Profile management commands.** `profile clone`, `profile diff`, `profile audit`, `profile list`, `profile install-mcp`. Follows from the above: nothing to manage.
- **MCP installation command.** MCP servers are declared in a profile's `mcp.json`. Init scripts mutate `cl9.agent.mcp` when needed; persistent additions require authoring a new profile. There is no `cl9 install-mcp`.
- **Declarative init format.** No TOML/YAML/JSON config that replaces `init.py`. A declarative format would either require shipping identity-provider plugins (the next non-goal) or a `command:` escape hatch that is strictly worse than Python — and either way it would force `init.py` to own the full config rather than being a thin overlay over the profile.
- **Identity-provider plugins.** No `cl9.secrets.from_op()`, `from_keyring()`, vault glue. Init scripts shell out to the relevant CLI. Keeping cl9 out of the identity space is the load-bearing constraint that lets `init.py` stay small and lets cl9 avoid every credential-store integration debate.
- **Typed `cl9.agent` schema.** `settings` and `mcp` are plain dicts, not dataclasses mirroring claude's schema. The schema churns upstream and typing it is not worth the maintenance.
- **`cl9 agent config` debug command.** A "print the resolved config without launching" command would be useful but not load-bearing for v1. Worth revisiting once the spawn pipeline is in place.
- **Profile templating.** Profile files are raw-copied. No `{{PROJECT_NAME}}` substitution. Per-session variability comes from claude's own env-var expansion in command strings (`${CL9_RUNTIME_DIR}`) and from `cl9.agent`. Existing template placeholders in the shipped default profile are removed as part of implementation.
- **Project relocatability.** Moving a project to a different path invalidates existing session runtimes and claude's session storage. The resume guardrail catches this with a clear error; nothing more.
- **Third-party init dependencies.** Init scripts get cl9's runtime — stdlib plus whatever cl9 itself depends on. Scripts that want richer functionality should shell out to CLIs rather than `pip install` into cl9's environment. PEP 723 inline script dependencies via `uv run --script` are a plausible future direction; not v1.

## Relationship to other ADRs

- **Supersedes ADR 0008's "Project-Local Working Copies Stay the Same" section.** The manifest + launch-adapter model from ADR 0008 is preserved; the project-local profile copy at `.cl9/profiles/<name>/` and the `_materialize_profile` flow are removed in favor of the per-session runtime directory.
- **Consolidates three prior drafts.** Earlier proposals on immutable profiles, session discovery and resume, and sealed sessions with init scripts are superseded in full by this ADR. The resume-working-directory fix and the immutable profile model are preserved; the session-discovery UX is reclassified as a non-architectural convenience and dropped from the ADR trail.

## References

- `claude --help` — `--bare` flag semantics and auth behavior
- ADR 0008 — Profile-Bound Agent Executables
- GitHub Issue #2 — Stale agent profiles after cl9 upgrade
- GitHub Issue #3 — Poor session overview and resume UX
