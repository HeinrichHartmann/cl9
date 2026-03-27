# ADR 0002: Plugin System Architecture

**Date**: 2026-03-27

**Status**: Accepted

## Context

cl9 needs to support extensible behaviors that can modify or enhance the core workflow without cluttering the main codebase. Key use cases include:

1. **Terminal multiplexer integration**: Custom window/pane layouts
2. **IDE integration**: Auto-open editors or tools
3. **Custom workflows**: Project-specific setup, pre-flight checks, cleanup
4. **Environment management**: Setup services, databases, background tasks

We need a plugin system that is:
- Simple for users to write and use
- Powerful enough for complex integrations
- Maintainable and debuggable
- Doesn't require packaging knowledge
- Allows both built-in (shipped) and user-created plugins

## Decision

Implement a **hybrid plugin system** using file-based discovery for user plugins and standard imports for built-in plugins.

### Architecture

**File-Based User Plugins**: `~/.config/cl9/plugins/*.py`
- Users drop Python files in this directory
- Automatically discovered and loaded at cl9 startup
- No installation or packaging required
- Full Python capabilities available

**Built-In Plugins**: `src/cl9/plugins/builtin/*.py`
- Ship with cl9
- Imported normally as Python modules
- Maintained as part of cl9 codebase
- Can be overridden by user plugins with same name

### Plugin Interface

Plugins are simple Python modules that export hook functions:

```python
"""Example plugin."""

def is_enabled(config: dict) -> bool:
    """Check if plugin should be active."""
    return config.get('enabled', False)

def on_enter(project_data: dict, env: dict, config: dict) -> bool:
    """Hook: entering project. Return True to take over execution."""
    return False

def on_agent(project_path: Path, config: dict) -> bool:
    """Hook: launching agent. Return True to take over execution."""
    return False

def pre_enter(project_data: dict, config: dict) -> None:
    """Hook: before enter validation."""
    pass

def post_enter(project_data: dict, config: dict) -> None:
    """Hook: after enter completes."""
    pass
```

**Required**: None (plugins implement only hooks they need)

**Convention**: Functions matching hook names are automatically discovered

### Hook Lifecycle

```
cl9 enter <project>
  ↓
load plugins (if not loaded)
  ↓
[pre_enter hooks] - all active plugins, observation only
  ↓
validate project exists
  ↓
setup environment (CL9_* vars)
  ↓
[on_enter hooks] - plugins can take over (return True)
  ↓
spawn shell or plugin handles it
  ↓
[post_enter hooks] - after execution completes
```

### Configuration

Global configuration in `~/.config/cl9/config.json`:

```json
{
  "version": "1",
  "plugins": {
    "example_plugin": {
      "enabled": true,
      "custom_option": "value"
    }
  },
  "hooks": {
    "on_enter": ["example_plugin"],
    "on_agent": []
  }
}
```

**Configuration features:**
- Per-plugin config sections (under `plugins.<plugin_name>`)
- Hook execution order configurable (under `hooks.<hook_name>`)
- Plugins receive their config in every hook call
- Plugins can provide DEFAULT_CONFIG that is merged with user config

### Plugin Discovery and Loading

```python
# src/cl9/plugins/loader.py

class PluginLoader:
    def load_all(self):
        # 1. Load built-in plugins from src/cl9/plugins/builtin/
        # 2. Load user plugins from ~/.config/cl9/plugins/
        # 3. Merge configs with defaults
        # 4. Cache hook functions for fast execution

    def run_hook(self, hook_name: str, *args) -> bool:
        # Execute hook in configured order
        # Return True if any plugin took over
```

**Loading uses**: `importlib.util.spec_from_file_location()`

**Error handling**: Plugin load failures are logged but don't crash cl9

### Built-In Plugins

Built-in plugins ship with cl9 and are maintained as part of the codebase. They are loaded from `src/cl9/plugins/builtin/` and follow the same interface as user plugins.

Examples of planned built-in plugins:
- Terminal multiplexer integration (tmux, zellij, etc.)
- IDE integration
- Project templates

See separate ADRs for specific built-in plugin designs.

## Consequences

### Positive

- **Simple user experience**: Drop Python file, no packaging needed
- **Full Python power**: Plugins can do anything Python can
- **Flexible**: Multiple plugins can coexist and compose
- **Maintainable**: Built-in plugins in codebase, user plugins isolated
- **Discoverable**: Plugins are just Python files in known location
- **Safe**: Plugin errors don't crash cl9
- **Extensible**: Easy to add new hooks as needs emerge
- **Pythonic**: Follows patterns from pytest, sphinx, airflow

### Negative

- **No dependency management**: User plugins can't declare dependencies
- **No versioning**: User plugins don't have version metadata
- **Namespace collisions**: Two plugins could define same hooks (mitigated by execution order)
- **Security**: User plugins run with full cl9 privileges (acceptable for local config)

### Trade-offs Accepted

- Simplicity over formal packaging: Users want to drop files, not create packages
- Power over safety: Python code execution is powerful but requires trust
- Convention over configuration: Hook discovery via naming convention

## Alternatives Considered

### Option 1: Entry Points Only

Use setuptools entry points for all plugins.

**Rejected because:**
- Too heavy for simple user scripts
- Requires packaging knowledge
- Installation overhead
- Poor UX for quick customizations

### Option 2: File-Based Only

No built-in plugins, everything file-based.

**Rejected because:**
- Built-in plugins (like tmux) should ship with cl9
- Code quality/testing harder for shipped plugins
- Would duplicate plugin loading code

### Option 4: Configuration-Based Only

Plugins defined purely in JSON/YAML config with limited capabilities.

**Rejected because:**
- Too limited for complex integrations (tmux, IDE)
- Would need to invent scripting language
- Python is already available and powerful

### Option 5: Subprocess/Shell Scripts

Plugins as shell scripts invoked via subprocess.

**Rejected because:**
- Less integration with cl9 internals
- Harder to share state/context
- Error handling more complex
- Python is more portable

## Implementation Notes

### Phase 1 (MVP)
1. Implement plugin loader infrastructure (`PluginLoader`, `PluginModule` classes)
2. Add global config support to Config class
3. Implement hook system in CLI commands (`pre_*`, `on_*`, `post_*` hooks)
4. Create plugin directory structure in user config
5. Document plugin API and create example plugins

### Phase 2 (Future)
- Built-in plugins (see separate ADRs)
- Plugin templates and examples
- Plugin testing framework
- Plugin command registration (`register_commands` hook)
- Optional: Add entry points support for formal plugins

### Future Possibilities

- Plugin can register custom CLI commands
- Plugin can contribute to `cl9 list` output
- Plugin lifecycle hooks (on_init, on_list, etc.)
- Plugin dependencies (one plugin requires another)
- Plugin marketplace/registry (curated community plugins)

## References

- pytest plugin system: https://docs.pytest.org/en/stable/how-to/writing_plugins.html
- importlib documentation: https://docs.python.org/3/library/importlib.html
- Sphinx extensions: https://www.sphinx-doc.org/en/master/development/tutorials/extending_build.html
