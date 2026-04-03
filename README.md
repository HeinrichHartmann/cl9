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
```

## Shell Completion

Enable tab completion for project names and commands by adding this to your shell configuration:

**Zsh (~/.zshrc):**
```zsh
source <(cl9 env zsh)
```

**Bash (~/.bashrc):**
```bash
source <(cl9 env bash)
```

**Fish (~/.config/fish/config.fish):**
```fish
cl9 env fish | source
```

Completions provide:
- Project name completion for `cl9 enter` and `cl9 remove`
- Command name completion
- Option/flag completion

## Synopsis

```
cl9 init [<path>] [-n|--name <name>]
cl9 list [-f|--format <format>]
cl9 remove <project>
cl9 enter <target> [-n|--name] [-p|--path]
cl9 agent
cl9 env <shell>
```

## Commands

### cl9 init

Initialize a cl9 project in a directory.

**Usage:**
```
cl9 init [<path>] [-n|--name <name>]
```

**Description:**

Registers a directory as a cl9 project. Creates a `.cl9/` subdirectory for project-local state and adds the project to the global registry.

If `<path>` is omitted, the current directory is used. If `--name` is not provided, the project name is derived from the directory name.

**Files Created:**
- `.cl9/` - Project-local state directory
  - Configuration
  - Session data
  - Agent environments

**Global State:**
- Project added to registry in XDG config directory
- Project path and metadata stored

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
```

---

### cl9 list

List all registered cl9 projects.

**Usage:**
```
cl9 list [-f|--format <format>]
```

**Description:**

Displays all projects registered in the cl9 global registry. Shows project names, locations, and basic metadata.

**Options:**
- `-f, --format <format>` - Output format (default: human-readable)
  - `markdown` or `md` - Human-readable markdown table (default)
  - `json` - JSON array of project objects
  - `tsv` - Tab-separated values

**Output (default format):**

Human-readable listing showing:
- Project name
- Project path
- Last accessed (if available)
- Active sessions (if any)

**Examples:**
```bash
# List all projects (default markdown format)
cl9 list

# List projects as JSON
cl9 list --format json

# List projects as TSV for scripting
cl9 list -f tsv
```

---

### cl9 remove

Remove a project from the registry.

**Usage:**
```
cl9 remove <project>
```

**Description:**

Removes a project from the cl9 global registry. This operation only affects the registry - it does not delete any files or directories.

The project's directory and `.cl9/` subdirectory remain intact. Use this command to clean up the registry when:
- A project was registered with the wrong name
- A project directory has been moved or deleted
- You no longer want cl9 to track a project

**Arguments:**
- `<project>` - Name of the registered project to remove

**Examples:**
```bash
# Remove a project from the registry
cl9 remove old-project

# The project files still exist, only the registry entry is removed
# To re-register, run cl9 init in the project directory
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

Launch an LLM agent in the current project.

**Usage:**
```
cl9 agent
```

**Description:**

Launches a Claude Code session in the current directory. Must be run from within a cl9 project directory (one containing a `.cl9/` subdirectory).

This command starts or resumes a Claude Code session using `claude --continue`.

**Requirements:**
- Must be in a directory with a `.cl9/` subdirectory
- `claude` command must be available in PATH

**Examples:**
```bash
# Typical workflow
cl9 enter myapp      # Enter project by name (spawns subshell)
cl9 agent            # Launch agent in project
# Work with the agent...
exit                 # Leave project context

# Quick session
cl9 enter ~/work/myapp && cl9 agent
```

---

### cl9 env

Output shell completion script for the specified shell.

**Usage:**
```
cl9 env <shell>
```

**Description:**

Generates shell-specific completion scripts for cl9. The output should be sourced in your shell configuration file to enable tab completion.

**Arguments:**
- `<shell>` - Shell type: `bash`, `zsh`, or `fish`

**Features:**
- Tab completion for project names in `cl9 enter` and `cl9 remove`
- Command name completion
- Option and flag completion

**Examples:**
```bash
# Zsh - add to ~/.zshrc
source <(cl9 env zsh)

# Bash - add to ~/.bashrc
source <(cl9 env bash)

# Fish - add to ~/.config/fish/config.fish
cl9 env fish | source

# Test completion without installing
source <(cl9 env zsh)  # Then try: cl9 enter <TAB>
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
  - Agent environments
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
