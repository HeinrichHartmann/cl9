# Statusline Debug Session — 2026-04-09

## Goal

Get a custom cl9 statusline rendering in Claude Code, launched via `cl9 agent spawn` with the Zalando profile.

## What we built

- **Zalando profile** at `~/.cl9/profiles/Zalando/` with `settings.json`, `mcp.json`, `statusline.py`
- **`cl9 profile import`** command (copy semantics, replaces old `cl9 profile add` which was symlink-based)
- **`agent_command`** in project config: project-level override for the agent launch command
- **Statusline** with cl9 badge, Zalando Z chip, project, session, short Bedrock model name, context bar, cost

## The debug journey

### 1. `--bare` strips everything

**Symptom:** Claude Code launched with `--bare` showed no statusline, no MCP servers, default status.

**Root cause:** `--bare` is designed for scripted `claude -p` one-shots. It strips hooks, MCP servers, statusline, and limits tools to Bash/Read/Edit.

**Fix:** Removed `--bare` from `ClaudeAdapter._base_command()`. The `--settings` and `--mcp-config` flags work fine without it.

### 2. `--settings` works but is invisible

**Symptom:** After removing `--bare`, still showed "API Usage Billing" in the welcome screen.

**Finding:** The welcome screen text is cosmetic — it always shows "API Usage Billing" regardless of auth method. Running `/status` inside Claude confirmed settings WERE loaded:
```
Auth token: apiKeyHelper
API key: apiKeyHelper
Anthropic base URL: https://zllm.data.zalan.do
Model: bedrock/anthropic.claude-sonnet-4-6
Setting sources: Command line arguments
```

### 3. Statusline exits with status 1

**Symptom:** `/status` confirmed settings loaded, but statusline never appeared. Default `● high · /effort` showed instead.

**Debug tool:** `claude --debug-file /tmp/cl9-debug.log` revealed:
```
StatusLine [python3 "${CL9_RUNTIME_DIR}/statusline.py"] completed with status 1
```

Also: `MCP server "sunrise-proxy" Connection failed: spawn zllm ENOENT`

### 4. Root cause: nix blocks python3

**Symptom:** `direnv exec ~/Projects/ZAMP python3 -c 'print("hi")'` → `error: tool 'python3' not found` (exit 1)

**Root cause:** ZAMP's `flake.nix` had `python3` commented out in `buildInputs`. Nix's pure shell blocks access to host tools like `/usr/bin/python3`, even though they exist on the filesystem. The error `tool 'python3' not found` is a **nix error**, not a shell "command not found" (which would be exit 127).

**Fix:** Added `python3` to ZAMP's `flake.nix` buildInputs.

**Same issue caused** `zllm ENOENT` for MCP servers — `zllm` wasn't on PATH in the Claude subprocess env until `agent_command` with `direnv exec` was wired up.

### 5. Bisect confirmed the fix

Replaced statusline.py with `print("HELLO WORLD from cl9")` → appeared in status bar. Restored full statusline → full rendering with badges, model name, context bar.

## Key findings about Claude Code

| Topic | Finding |
|---|---|
| `--bare` | Too restrictive for interactive sessions. Strips statusline, MCP, hooks, most tools. |
| `--settings` | Works. Merges on top of user/project settings. Higher precedence than `~/.claude/settings.json`. |
| `--mcp-config` | Works. Loads MCP servers from file. |
| `statusLine` | Command runs in a shell, expands env vars (`${CL9_RUNTIME_DIR}`). Must exit 0 and print to stdout. |
| `/status` | Shows active settings sources, auth method, model — essential debugging tool. |
| `--debug-file` | Logs statusline exit codes, MCP errors, etc. Critical for diagnosing invisible failures. |
| Subprocess env | Claude Code inherits its parent's env for subprocesses (statusline, MCP). No sanitization observed. |

## Architecture decisions made

1. **`agent_command` in project config** — project-level shell string that replaces the agent executable. Runs through `sh -ic` with `CL9_*` env vars available for shell expansion. Keeps direnv/nix knowledge in the project, out of cl9 core.

2. **Profile as standalone snapshot** — `cl9 profile import` copies (not symlinks) the profile directory. `--force` replaces. Authoring copy lives in project, installed copy in `~/.cl9/profiles/`.

3. **`--bare` removed** — `--settings` and `--mcp-config` are sufficient without it. Claude Code's normal discovery chain coexists with our injected settings.
