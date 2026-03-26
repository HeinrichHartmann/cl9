# cl9 - LLM Session Manager

`cl9` (Claw 9) is an opinionated LLM session manager for organizing AI-assisted work across isolated project contexts. Named after Plan 9, it embraces the philosophy of deliberate copying and context isolation over sharing and symlinking.

Projects are self-contained workspaces where everything relevant to a context lives together. The same source repository or document can exist in multiple projects as independent copies, enabling parallel exploration and later merging of state.

## Installation

```bash
# Local development install
uv tool install ~/p-workbench/src/cl9

# From GitHub
uv tool install git+https://github.com/username/cl9
```

## Synopsis

```
cl9 init [<project-name>]
cl9 enter <project>
```

## Commands

### cl9 init

Initialize a cl9 project in the current directory.

**Usage:**
```
cl9 init [<project-name>]
```

**Description:**

Registers the current directory as a cl9 project. Creates a `.cl9/` subdirectory for project-local state and adds the project to the global registry.

If `<project-name>` is provided, the project is registered with that name. Otherwise, the directory name is used.

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
# Initialize project with directory name
cd ~/work/my-app
cl9 init

# Initialize with explicit name
cd ~/repos/complicated-project-name
cl9 init myapp
```

---

### cl9 enter

Enter a project context and launch an LLM session.

**Usage:**
```
cl9 enter <project>
```

**Description:**

Switches to the specified project's directory and launches a Claude Code session. If a previous session exists for this project, it is resumed automatically.

The project must have been previously initialized with `cl9 init`.

**Arguments:**
- `<project>` - Name of the registered project to enter

**Behavior:**
- Changes working directory to project location
- Sets up project-local environment
- Launches `claude --continue` in project context
- Resumes previous session if available

**Examples:**
```bash
# Enter a project
cl9 enter myapp

# Enter with full project name
cl9 enter my-application
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
