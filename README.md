# cl9 - LLM Session Manager

`cl9` (Claw 9) is an opinionated LLM session manager for organizing AI-assisted work across isolated project contexts. Named after Plan 9, it embraces the philosophy of deliberate copying and context isolation over sharing and symlinking.

Projects are self-contained workspaces where everything relevant to a context lives together. The same source repository or document can exist in multiple projects as independent copies, enabling parallel exploration and later merging of state.

## Installation

```bash
# From this checkout
make install

# Local development install
uv tool install --force --reinstall ~/p-workbench/src/cl9

# From GitHub
uv tool install git+https://github.com/username/cl9
```

## Development

```bash
# Run the CLI test suite
make test

# Run lint checks
make lint
```

## Shell Completion

Enable tab completion for project names and commands by adding this to your shell configuration:

**Zsh (~/.zshrc):**
```zsh
source <(cl9 completion zsh)
```

**Bash (~/.bashrc):**
```bash
source <(cl9 completion bash)
```

**Fish (~/.config/fish/config.fish):**
```fish
cl9 completion fish | source
```

Completions provide:
- Project name completion for `cl9 enter` and `cl9 project remove`
- Command name completion
- Option/flag completion

## Synopsis

```
cl9 init [<path>] [-n|--name <name>] [-t|--type <type>] [--force]
cl9 enter <target> [-n|--name] [-p|--path]
cl9 run <command> [args...]
cl9 agent
cl9 agent spawn [--name <name>] [-p|--profile <profile>]
cl9 agent continue [<target>]
cl9 agent fork <target> [--name <name>] [-p|--profile <profile>]
cl9 session list
cl9 session prune [--older-than <days>d]
cl9 session delete <target> [--force]
cl9 man
cl9 completion <bash|zsh|fish>
cl9 project init [<path>] [-n|--name <name>] [-t|--type <type>] [--force]
cl9 project enter <target> [-n|--name] [-p|--path]
cl9 project run <command> [args...]
cl9 project register [<path>]
cl9 project list [-f|--format <format>]
cl9 project remove <project>
cl9 project prune
```

## Commands

### cl9 init

Initialize a cl9 project in a directory.

**Usage:**
```
cl9 init [<path>] [-n|--name <name>] [-t|--type <type>] [--force]
```

**Description:**

Initializes a directory as a local cl9 project. Creates a `.cl9/` subdirectory and applies an environment template.

If `<path>` is omitted, the current directory is used. If `--name` is not provided, the project name is derived from the directory name. If `--type` is not provided, cl9 uses the configured default environment type, or `default`.

`cl9 init` does not add the project to the global registry. Use `cl9 project register` if you want the project to appear in `cl9 project list` and be enterable by name.

If the target is already initialized, `cl9 init` becomes a preview command and shows what would be changed. Use `cl9 init --force` to re-apply the template and overwrite matching files.

**Options:**
- `-n, --name <name>` - Explicit project name
- `-t, --type <type>` - Environment type to apply (`default` or `minimal`, plus user/local templates)
- `--force` - Re-apply the selected template to an initialized project

**Files Created:**
- `.cl9/` - Project-local state directory
  - Configuration
  - `profiles/default/CLAUDE.md` default agent profile
  - `env/state.json` tracking delivered environment files
- `src/`, `doc/`, `data/` - Created by the `default` environment type
- `README.md`, `MEMORY.md`, `flake.nix`, `.envrc` - Created by the `default` environment type

**Examples:**
```bash
# Initialize the current directory with its directory name
cd ~/work/my-app
cl9 init
cl9 init .

# Initialize another directory, deriving the name from the path
cl9 init ~/projects/foo

# Initialize with an explicit name
cl9 init ~/repos/complicated-project-name --name myapp

# Initialize a minimal project
cl9 init ~/tmp/scratch --type minimal

# Register it in the global registry afterwards
cl9 project register ~/tmp/scratch

# Preview changes for an existing initialized project
cl9 init ~/tmp/scratch

# Re-apply the template and overwrite matching files
cl9 init ~/tmp/scratch --force
```

---

### cl9 project

Manage the global project registry.

**Usage:**
```
cl9 project register [<path>]
cl9 project list [-f|--format <format>]
cl9 project remove <project>
cl9 project prune
```

**Description:**

`cl9 project register` adds an initialized project directory to the global registry by checking for `.cl9/config.json`. This is the command to use after moving an existing project or when you want a locally initialized project to become discoverable by name.

`cl9 project list` shows registered projects.

`cl9 project remove` removes a project from the registry only.

`cl9 project prune` removes registry entries whose directories no longer exist or no longer contain `.cl9/config.json`.

**Examples:**
```bash
# Register an initialized project
cl9 project register ~/work/my-app

# List registered projects
cl9 project list

# Remove a project from the registry
cl9 project remove old-project

# Remove stale registrations
cl9 project prune

# The project files still exist; only the registry entry is removed
```

---

### cl9 enter

Enter a project context by spawning a subshell in its directory.

**Usage:**
```
cl9 enter <target> [-n|--name] [-p|--path]
```

**Description:**

Enters a project by spawning a new shell session in the project's directory. This creates an isolated shell environment where you can work on the project.

Use `exit` or press Ctrl+D to leave the project context and return to your original shell.

`<target>` is resolved as a registry name first, then as a filesystem path containing `.cl9/`, unless a flag forces one mode.

**Arguments:**
- `<target>` - Project name or filesystem path

**Options:**
- `-n, --name` - Force registry-name lookup
- `-p, --path` - Force filesystem-path lookup

**Behavior:**
- Resolves `<target>` as a registry name or filesystem path
- Checks project directory and `.cl9/` subdirectory exist
- Updates last accessed timestamp
- Changes to project directory
- Spawns a new shell using `$SHELL` (shell-agnostic)
- Sets environment variables:
  - `CL9_PROJECT` - Project name from the registry or local `.cl9/config.json`
  - `CL9_PROJECT_PATH` - Full path to project
  - `CL9_ACTIVE=1` - Indicates active cl9 context

**Examples:**
```bash
# Enter a registered project by name
cl9 enter myapp

# Enter an initialized directory by path, even if it is not registered
cl9 enter ~/projects/foo

# Force a specific interpretation
cl9 enter --name myapp
cl9 enter --path ./some-dir
```

---

### cl9 run

Run a command in the current project environment.

**Usage:**
```
cl9 run <command> [args...]
cl9 project run <command> [args...]
```

**Description:**

Runs the command inside the current project's execution environment. `bin/` at the project root is prepended to `PATH`, `CL9_*` environment variables are set, and the command is executed through the user's shell from the project root.

**Examples:**
```bash
cl9 run snapshot
cl9 project run lint --fix
```

---

### cl9 agent

Launch an isolated Claude Code agent in the current project.

**Usage:**
```
cl9 agent
cl9 agent spawn [--name <name>] [-p|--profile <profile>]
cl9 agent continue [<target>]
cl9 agent fork <target> [--name <name>] [-p|--profile <profile>]
```

**Description:**

Launches or resumes Claude Code sessions tracked in the current project's `.cl9/state.db`.

The command works from any subdirectory within a cl9 project by walking up to the nearest project root containing `.cl9/config.json`.

Profiles are resolved from:
- `.cl9/profiles/<name>/` as the project-local working copy
- `~/.cl9/profiles/<name>/` for user-installed profiles
- built-in profiles shipped with `cl9`

`cl9 agent spawn` creates a new tracked session. `cl9 agent continue` resumes a previously tracked session, and `cl9 agent fork` forks an existing session into a new one.

There is no separate `cl9 profile` command. Built-in profiles are documented by `cl9`, and additional profiles are installed by placing directories under `~/.cl9/profiles/`.

When a profile is used, `cl9` materializes a working copy in `.cl9/profiles/<name>/`. That project-local copy is managed state. It may be mutated by the agent or by `cl9` and should not be edited by the user directly.

Claude is launched with project-local profile files layered on top of user-level Claude settings:

```bash
claude --setting-sources user \
  --append-system-prompt-file .cl9/profiles/default/CLAUDE.md \
  [--settings .cl9/profiles/default/settings.json] \
  [--mcp-config .cl9/profiles/default/mcp.json]
```

This keeps Claude logged in through its normal user-level config while letting each project add project-local profile overlays.

**Requirements:**
- Must be inside a cl9 project directory or one of its subdirectories
- `claude` command must be available in PATH

**Examples:**
```bash
# Typical workflow
cl9 enter myapp      # Enter project by name (spawns subshell)
cl9 agent spawn      # Launch agent in project

# From a nested subdirectory
cd ~/work/myapp/src/deep/nested
cl9 agent spawn

# Resume the latest tracked session
cl9 agent continue

# Fork a tracked session
cl9 agent fork latest --name branch-a

# Quick session
cl9 enter ~/work/myapp && cl9 agent spawn
```

---

### cl9 session

Manage project-local tracked sessions.

**Usage:**
```
cl9 session list
cl9 session prune [--older-than <days>d]
cl9 session delete <target> [--force]
```

**Description:**

These commands operate on `cl9`'s local session metadata in `.cl9/state.db`. They do not remove Claude-owned history from `~/.claude`.

**Examples:**
```bash
cl9 session list
cl9 session prune --older-than 30d
cl9 session delete latest
```

---

### cl9 man

Print the complete manual generated from the current CLI command tree.

**Usage:**
```
cl9 man
```

**Description:**

Outputs a manpage-style overview of the current `cl9` command set, including nested subcommands and option summaries. This is generated directly from the Click command definitions so it stays aligned with the implemented CLI.

**Examples:**
```bash
# Print the full manual
cl9 man
```

---

### cl9 completion

Output shell completion script for the specified shell.

**Usage:**
```
cl9 completion <shell>
```

**Description:**

Generates shell-specific completion scripts for cl9.

**Examples:**
```bash
# Zsh - add to ~/.zshrc
source <(cl9 completion zsh)

# Bash - add to ~/.bashrc
source <(cl9 completion bash)

# Fish - add to ~/.config/fish/config.fish
cl9 completion fish | source

# Test completion without installing
source <(cl9 completion zsh)  # Then try: cl9 enter <TAB>
```

## Project Structure

A cl9 project consists of:

**User-managed files:**
- Source code repositories (copied, not symlinked)
- Documents and resources (local copies)
- Any other project-relevant files

**cl9-managed files:**
- `.cl9/` - Project-local state (managed by cl9)
  - Session data
  - Agent profile working copies in `.cl9/profiles/`
  - Project configuration

## Philosophy

**Deliberate Copying**
- Resources are copied into projects, not shared via symlinks
- Same source repo can exist in multiple projects independently
- Documents (including Google Docs) are copied locally
- Enables parallel evolution of state

**Context Isolation**
- Each project is a complete, self-contained context
- Everything relevant to the work lives in the project directory
- No implicit dependencies on external state

**Explicit Over Automatic**
- User explicitly initializes projects
- User explicitly enters contexts
- No magic, no hidden state

## Terminology

**Agent:** An LLM tool instance (Claude Code, GitHub Copilot CLI, Codex, etc.) - any AI assistant running inside a CLI harness. Multiple agents can operate within a single project context.

## State Management

**Global State** (XDG directories, managed by cl9):
- `~/.config/cl9/` - Configuration and project registry
- `~/.local/share/cl9/` - Shared data
- `~/.cache/cl9/` - Cache files

**Project State** (`.cl9/`, managed by cl9):
- Session data
- Agent environments
- Project-specific configuration

**Project Files** (user-managed):
- Source code
- Documents
- Resources
- User controls organization

## See Also

- `docs/adr/` - Architecture Decision Records
- Claude Code documentation

## License

[To be determined]
