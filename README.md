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
cl9 init [<path>] [-n|--name <name>] [-t|--type <type>]
cl9 update [--diff] [--force]
cl9 enter <target> [-n|--name] [-p|--path]
cl9 agent
cl9 agent spawn [--no-continue]
cl9 man
cl9 completion <bash|zsh|fish>
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
cl9 init [<path>] [-n|--name <name>] [-t|--type <type>]
```

**Description:**

Initializes a directory as a local cl9 project. Creates a `.cl9/` subdirectory and applies an environment template.

If `<path>` is omitted, the current directory is used. If `--name` is not provided, the project name is derived from the directory name. If `--type` is not provided, cl9 uses the configured default environment type, or `default`.

`cl9 init` does not add the project to the global registry. Use `cl9 project register` if you want the project to appear in `cl9 project list` and be enterable by name.

If any generated file or directory already exists in the target location, initialization fails before writing and asks you to move the conflicting paths out of the way.

**Options:**
- `-n, --name <name>` - Explicit project name
- `-t, --type <type>` - Environment type to apply (`default` or `minimal`, plus user/local templates)

**Files Created:**
- `.cl9/` - Project-local state directory
  - Configuration
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
```

---

### cl9 update

Update the current project's scaffolded environment from its tracked template.

**Usage:**
```
cl9 update [--diff] [--force]
```

**Description:**

Reapplies the environment template recorded in `.cl9/env/state.json`.

**Options:**
- `--diff` - Show what would change without modifying files
- `--force` - Overwrite files that have been modified by the user

By default, `cl9 update`:
- updates tracked files that still match their previously delivered contents
- re-adds missing tracked files
- skips tracked files modified by the user
- skips existing untracked files instead of overwriting them silently

**Examples:**
```bash
# Preview updates
cl9 update --diff

# Force-reset tracked files back to the template
cl9 update --force
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

### cl9 agent

Launch an isolated Claude Code agent in the current project.

**Usage:**
```
cl9 agent
cl9 agent spawn [--no-continue]
```

**Description:**

Launches a Claude Code session with the normal user account/session state plus project-local overlays from `.cl9/claude/`.

The command works from any subdirectory within a cl9 project by walking up to the nearest project root containing `.cl9/config.json`.

`cl9 agent` shows the available subcommands. `cl9 agent spawn` runs:

```bash
claude --setting-sources user \
  --append-system-prompt-file .cl9/claude/CLAUDE.md \
  [--settings .cl9/claude/settings.json] \
  [--mcp-config .cl9/claude/mcp.json] \
  --continue
```

This keeps Claude logged in through its normal user-level config while letting each project add its own instructions and optional settings overrides.

Use `--no-continue` to omit the `--continue` flag.

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

# Start without --continue
cl9 agent spawn --no-continue

# Work with the agent...
exit                 # Leave project context

# Quick session
cl9 enter ~/work/myapp && cl9 agent spawn
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
  - Agent configuration in `.cl9/claude/`
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
