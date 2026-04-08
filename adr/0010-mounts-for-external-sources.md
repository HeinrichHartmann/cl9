# ADR 0010: Mounts for External Profile/MCP/Skill Sources

**Date**: 2026-04-08

**Status**: Accepted

## Context

cl9 ships a small number of built-in profiles (`default`, `codex`) and supports user-authored profiles at `~/.cl9/profiles/<name>/` via `cl9 profile add`. This covers two of the three realistic cases:

1. **Built-in profiles** — curated defaults shipped with cl9 itself.
2. **User-local profiles** — one-off profiles a user writes by hand for a single machine.

The missing case is **shared profile collections**: a git repository containing several profiles that one author maintains and multiple machines or users consume. The current options for this case are both unsatisfying:

- Clone the repo somewhere and run `cl9 profile add` per profile. Manual, easy to forget, no update path, no `cl9 profile remove-all`.
- `git clone` the repo directly into `~/.cl9/profiles/` as a single mega-directory. Conflates "shared pack" with "hand-written locals," and `cl9 profile update` still operates on one profile at a time.

The same gap will exist shortly for MCP server definitions and skills. These three artifact types have the same distribution problem: they are directory-shaped, they are usually authored in sets rather than individually, and the natural distribution mechanism is a git repository.

Rather than solve the profile case and then rediscover it twice, this ADR defines one primitive that works for all three.

## Decision

### The mount primitive

A **mount** is a git clone of an external repository placed under a known location, whose contents are discoverable by cl9's existing resolution paths. Mounts live under:

```
~/.cl9/mounts/<name>/
```

Each mount may contain any subset of the following subdirectories:

```
~/.cl9/mounts/<name>/
  profiles/
    <profile-name>/
      manifest.json
      CLAUDE.md
      ...
  mcps/                    # future
    <mcp-name>/
      ...
  skills/                  # future
    <skill-name>/
      ...
```

Absence of any subdirectory is fine. A mount containing only `profiles/` is valid; so is one containing only `skills/`.

### Discovery and precedence

Profile resolution becomes a three-tier search:

1. **User-local** — `~/.cl9/profiles/<name>/`
2. **Mounted** — `~/.cl9/mounts/*/profiles/<name>/`, iterated in sorted order by mount name; first match wins
3. **Built-in** — profiles shipped with cl9

Earlier tiers shadow later ones. A user-local profile with the same name as a mounted profile wins; a mounted profile with the same name as a built-in profile wins. This mirrors how `PATH` lookups and shell function precedence work, and matches user intuition: what you put in front of cl9 is what cl9 sees first.

When MCPs and skills are added, they will follow the same three-tier discovery rule, with `~/.cl9/mcps/` and `~/.cl9/skills/` as the user-local layer.

### Why "mount"

The name is chosen deliberately. `cl9`'s OS framing treats the project as a namespace shared by humans and LLM agents (see README §Naming & Inspiration). In that vocabulary, attaching an external namespace into your own is called *mounting*, not *tapping* or *adding a repo*. The word also future-proofs the command: `cl9 mount` is the right primitive regardless of what artifact type the mounted repository provides. When skills ship, no new verb is needed.

Alternatives considered and rejected:

- `cl9 tap` — cleanly modeled after Homebrew but wrong metaphor; taps are beer kegs, not namespaces, and the analogy does not extend to multiple artifact types.
- `cl9 profile pull` — reads as fetching a single profile; hard to generalize when the same repo also contains MCPs.
- `cl9 pack add` — generic but carries no design signal.

### Commands

```
cl9 mount add <spec> [--name <name>]
cl9 mount list
cl9 mount update [<name>]
cl9 mount remove <name>
```

`add` clones a git repository into `~/.cl9/mounts/<name>/`. The `<spec>` is a repo URL or filesystem path with an optional trailing ref, in the form `<repo>[@<ref>]`. See "Ref pinning and tree-ish specs" below. The mount name defaults to the repository basename (the last path component of the URL with any trailing `.git` stripped, and any trailing `@<ref>` dropped first). Already-existing names fail loudly rather than silently overwriting.

`list` shows each mount with its origin remote, its pinned ref (if any), and a count of profiles, mcps, and skills it exposes.

`update` fetches from origin and resets the mount to the intended state, discarding any local modifications or untracked files (`git fetch` + `git reset --hard <target>` + `git clean -fdx`). The target depends on whether the mount is pinned (see below). There is no merge, no rebase, no lineage to preserve: mounts are read-only extensions of cl9, not working copies, and "update" means "drop whatever is here and check out the intended state." With no argument, `update` iterates all mounts.

`remove` deletes the mount directory outright.

### Ref pinning and tree-ish specs

A mount spec can optionally specify a tree-ish — any ref git can resolve: a branch name, a tag, or a commit SHA — using an `@` suffix:

```
cl9 mount add /tmp/my-profiles
cl9 mount add /tmp/my-profiles@main
cl9 mount add git@github.com:foo/bar@v1.2.3
cl9 mount add https://github.com/foo/bar@abc1234
```

**Syntax** — this follows the Go modules (`go get github.com/foo/bar@v1.2.3`) and pip (`pip install git+https://github.com/foo/bar@v1.2.3`) convention. The separator is the **last `@` in the spec that is not part of an SSH URL**: because SSH URLs use `user@host:path`, any `@` whose trailing segment contains `:` is treated as part of the URL, not as a ref separator. This is the same disambiguation pip does, and it is sufficient to unambiguously parse all realistic inputs including `git@github.com:foo/bar@v1.2.3`. Git itself has no standard single-string form for repo-plus-ref, so adopting the established dependency-tool convention gives users something they can guess from muscle memory.

**Clone strategy** — unpinned mounts use a shallow `--depth 1` clone for speed. Pinned mounts use a full clone, because a shallow clone cannot check out an arbitrary commit SHA or tree-ish that is not the tip of a named branch. This is a deliberate trade: pinning is a less common case and the full-clone cost is paid once, per-mount.

**Storage** — the pinned ref is recorded inside the clone as `git config cl9.mountRef <ref>`. It travels with the mount directory, survives git operations, and is retrievable via the same `git config --get` mechanism cl9 uses for the origin URL. No side-file, no index to keep in sync with the filesystem.

**Update semantics depend on the ref type** — and mirror what git itself would do:

- **Unpinned** — `git fetch --depth 1 origin` + `git reset --hard FETCH_HEAD`. Follows whatever the remote's default branch currently points to.
- **Pinned to a branch** (e.g. `main`) — `git fetch origin` + `git reset --hard origin/main`. Follows the branch tip. If the branch is force-pushed, the update sees the new state.
- **Pinned to a tag** — `git fetch origin` + `git reset --hard <tag>`. Typically a no-op, unless the tag has been moved upstream (which is rare and intentional).
- **Pinned to a commit SHA** — `git fetch origin` + `git reset --hard <sha>`. Always a no-op as far as the checked-out content is concerned: the commit cannot move. The fetch is still performed so that the SHA remains present in the local object database after any upstream force-push.

A user who wants "follow upstream automatically" pins to a branch or omits the ref. A user who wants "frozen forever" pins to a tag or SHA. The same one-argument primitive covers both.

This also gives an **immediate unblock for local profile pools**: `cl9 mount add /path/to/my-profiles` works today for any on-disk git repository. No publishing required, no network needed. A team can share a profile pool as a path on a shared volume, a personal dotfiles directory, or a local checkout during development.

### What lives in a mount

Mounts contain source-of-truth directories — the same format `cl9` already uses elsewhere. A profile in a mount is a directory with `manifest.json`, `CLAUDE.md`, optional `settings.json`, and optional `mcp.json`; it is indistinguishable from a built-in profile or a user-local profile except for its path. No mount-specific manifest, no packaging wrapper, no registry metadata. Authors maintain their profile collection as an ordinary directory of directories under version control and cl9 consumes it as-is.

This means the repository layout is the contract. A mount author who restructures their repo breaks consumers in the same way a library author who renames a public function breaks callers; the fix is the same (version discipline, or a tag).

## Consequences

### Good

- **Zero-friction sharing.** A user publishes `cl9-profiles` as a public git repo and consumers run `cl9 mount add <url>` to pick up every profile in it at once. Updates are `cl9 mount update` away.
- **One vocabulary for three artifact types.** Mounts unify profile, MCP, and skill distribution under a single command. Adding MCPs or skills later is a discovery-layer change, not a new CLI surface.
- **No configuration file.** Mounts are discoverable from the filesystem alone. `~/.cl9/mounts/` is the source of truth; there is no `mounts.toml` to keep in sync.
- **Local-first precedence is preserved.** Existing user-local profiles keep winning over anything a mount provides, so adding a mount cannot silently change the behavior of an already-working project.
- **The OS metaphor stays coherent.** The command name reinforces the framing documented in the README and the ADR sequence.

### Neutral

- **Shallow, read-only clones by default; full clones when pinned.** Unpinned mounts use `--depth 1` for speed. Pinned mounts use a full clone because arbitrary tree-ish refs can't be shallow-fetched reliably. Either way, `update` always resets to the intended state. Mounts are not working copies; users are not expected to edit, branch, or commit inside `~/.cl9/mounts/`. Anything you put there is discarded on the next `cl9 mount update`. If you want to hack on a profile, author it in `~/.cl9/profiles/` (where user-local takes precedence) and let the mount serve the upstream version.
- **No lockfile.** There is no `mounts.lock` listing resolved commits across all mounts. Individual pinned mounts behave like a one-line lockfile for themselves (the SHA in `git config cl9.mountRef`), but there is no global "freeze everything" command and no `cl9 mount status` showing drift. If multi-mount reproducibility becomes important, a lockfile can be added without changing the primitive.

### Bad

- **Name collisions are possible.** Two mounts can define a profile with the same name. The precedence rule is "first mount in sorted order wins," which is deterministic but not visible to the user unless they run `cl9 profile list`. An explicit warning could be added; for now, the honest answer is "don't name your profiles after someone else's."
- **Trust is implicit.** `cl9 mount add` runs `git clone` against an arbitrary URL. The mounted content is then read at every `cl9 agent spawn` via profile materialization. A malicious mount can ship a profile whose `init.py` executes arbitrary code. This is the same trust model as `brew tap`, `asdf plugin add`, and `pip install git+https://...`, and the same mitigation applies: only mount sources you would clone by hand. cl9 does not attempt to sandbox mounted code.
- **No uninstall cleanup of dangling references.** If a session was spawned with a mounted profile and the user later removes that mount, resuming the session will fail with "profile not found." This is the same failure mode as removing a user-local profile while sessions still reference it, and is acceptable: the runtime directory already contains the materialized snapshot, and the fix is to remount or fork.

## Resolved design questions

- **Global vs. per-project scope** — mounts are global, not per-project. They extend cl9 itself, not individual projects. A user who wants different mount contents per project should use user-local profiles (`~/.cl9/profiles/`) to shadow mount entries on a case-by-case basis; there is no reason to duplicate the entire mount registry per project.
- **Update semantics** — no merges, no lineage preservation. `cl9 mount update` does a hard checkout of the intended state (remote HEAD, or the pinned ref) and wipes any local modifications in the mount directory. Mounts are read-only extensions of cl9; treating them like working copies would invite divergence with no benefit.
- **Ref pinning** — supported inline via `<spec>@<ref>`, matching the Go modules (`go get github.com/foo/bar@v1.2.3`) and pip (`pip install git+https://...@v1.2.3`) convention. Split on the last `@` whose tail does not contain `:`, which is enough to disambiguate SSH URLs. Git itself has no standard inline form, so borrowing from the dependency-fetching ecosystems gives users a shape they already know.
- **Follow vs. freeze on update for ref-pinned mounts** — follow. A branch pin tracks the branch tip on update; a tag or commit pin is idempotent (the fetch happens but the reset is a no-op). This matches git's own mental model: if you wanted a frozen state you would have named a SHA, and if you named a branch you probably want its latest. Freeze-on-add (resolve-at-mount-time) was rejected because it makes `cl9 mount <url>@main` behave differently from `git clone -b main <url>`, which would be surprising.
- **Ref storage** — `git config cl9.mountRef <ref>` inside the mount's clone. Lives with the repository, survives all git operations, and requires no side-file or registry. Visible via `cl9 mount list` in the `ref:` line and via `git config --get cl9.mountRef` for direct inspection.

## Open questions

None currently; everything the ADR set out to resolve is resolved above. Revisit when the MCP or skill integration lands (for example: do MCP mounts need a different discovery rule, or a per-server manifest, or is `mcps/<name>/` enough?).
