"""Plugin loading and management."""

import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Any, Callable


class PluginLoadError(Exception):
    """Raised when a plugin fails to load."""
    pass


class PluginModule:
    """Wrapper for a loaded plugin module."""

    def __init__(self, name: str, module: Any, config: dict):
        """Initialize plugin module wrapper.

        Args:
            name: Plugin name
            module: The loaded Python module
            config: Plugin configuration dict
        """
        self.name = name
        self.module = module
        self.config = config
        self._cache_hooks()

    def _cache_hooks(self):
        """Cache available hook functions from module."""
        self.hooks: Dict[str, Callable] = {}

        hook_names = [
            'is_enabled',
            'on_enter',
            'on_agent',
            'pre_enter',
            'post_enter',
            'pre_agent',
            'post_agent',
            'register_commands'
        ]

        for hook_name in hook_names:
            if hasattr(self.module, hook_name):
                func = getattr(self.module, hook_name)
                if callable(func):
                    self.hooks[hook_name] = func

    def is_enabled(self) -> bool:
        """Check if plugin is enabled.

        Returns:
            True if plugin should be active
        """
        # If plugin has is_enabled function, use it
        if 'is_enabled' in self.hooks:
            try:
                return self.hooks['is_enabled'](self.config)
            except Exception as e:
                print(f"Warning: Plugin {self.name} is_enabled() failed: {e}")
                return False

        # Otherwise check config
        return self.config.get('enabled', False)

    def call_hook(self, hook_name: str, *args, **kwargs) -> Any:
        """Call a hook if it exists.

        Args:
            hook_name: Name of the hook to call
            *args: Positional arguments
            **kwargs: Keyword arguments (config is automatically added)

        Returns:
            Hook return value, or None if hook doesn't exist
        """
        if hook_name not in self.hooks:
            return None

        try:
            # Always pass config as last positional argument
            return self.hooks[hook_name](*args, self.config, **kwargs)
        except Exception as e:
            print(f"Error: Plugin {self.name}.{hook_name}() failed: {e}")
            return None


class PluginLoader:
    """Discovers and loads plugins."""

    def __init__(self, global_config: dict):
        """Initialize plugin loader.

        Args:
            global_config: Global configuration dict from config.json
        """
        self.global_config = global_config
        self.plugins: Dict[str, PluginModule] = {}

    def load_all(self, plugins_dir: Path):
        """Load all plugins (built-in and user).

        Args:
            plugins_dir: Path to user plugins directory
        """
        self._load_builtin_plugins()
        self._load_user_plugins(plugins_dir)

    def _load_builtin_plugins(self):
        """Load built-in plugins from src/cl9/plugins/builtin/."""
        builtin_dir = Path(__file__).parent / "builtin"

        if not builtin_dir.exists():
            return

        self._load_plugins_from_dir(builtin_dir, prefix="builtin:")

    def _load_user_plugins(self, plugins_dir: Path):
        """Load user plugins from config directory.

        Args:
            plugins_dir: Path to user plugins directory
        """
        if not plugins_dir.exists():
            return

        self._load_plugins_from_dir(plugins_dir)

    def _load_plugins_from_dir(self, directory: Path, prefix: str = ""):
        """Load all .py files from directory as plugins.

        Args:
            directory: Directory to scan for plugins
            prefix: Optional prefix for plugin names (e.g., "builtin:")
        """
        for plugin_file in directory.glob("*.py"):
            # Skip private files and __init__.py
            if plugin_file.name.startswith("_"):
                continue

            plugin_name = prefix + plugin_file.stem

            try:
                module = self._load_module(plugin_name, plugin_file)

                # Get plugin config from global config
                # Try with and without prefix
                config_name = plugin_file.stem  # Without prefix
                plugin_config = self.global_config.get('plugins', {}).get(
                    config_name, {}
                )

                # Merge with DEFAULT_CONFIG if plugin provides one
                if hasattr(module, 'DEFAULT_CONFIG'):
                    default_config = module.DEFAULT_CONFIG
                    plugin_config = {**default_config, **plugin_config}

                # Create plugin module wrapper
                self.plugins[plugin_name] = PluginModule(
                    plugin_name, module, plugin_config
                )

            except PluginLoadError as e:
                # Plugin explicitly failed to load (e.g., dependency missing)
                print(f"Info: Plugin {plugin_name} not loaded: {e}")
            except Exception as e:
                # Unexpected error
                print(f"Warning: Failed to load plugin {plugin_name}: {e}")
                import traceback
                traceback.print_exc()

    def _load_module(self, name: str, path: Path) -> Any:
        """Load a Python module from file path.

        Args:
            name: Module name for import system
            path: Path to .py file

        Returns:
            Loaded module object

        Raises:
            PluginLoadError: If plugin explicitly failed to load
            ImportError: If module cannot be loaded
        """
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create spec for {path}")

        module = importlib.util.module_from_spec(spec)

        # Add to sys.modules so plugin can import from cl9
        sys.modules[name] = module

        try:
            spec.loader.exec_module(module)
        except PluginLoadError:
            # Plugin explicitly failed (e.g., missing dependency)
            # Remove from sys.modules
            sys.modules.pop(name, None)
            raise
        except Exception as e:
            # Unexpected error during module execution
            sys.modules.pop(name, None)
            raise ImportError(f"Failed to execute module {name}: {e}")

        return module

    def get_active_plugins(self) -> List[PluginModule]:
        """Get list of enabled plugins.

        Returns:
            List of active PluginModule instances
        """
        return [p for p in self.plugins.values() if p.is_enabled()]

    def run_hook(self, hook_name: str, *args, **kwargs) -> bool:
        """Run a hook across all active plugins.

        Plugins are run in the order specified in global_config['hooks'][hook_name],
        followed by any remaining active plugins.

        Args:
            hook_name: Name of the hook (e.g., 'on_enter')
            *args: Arguments to pass to hook
            **kwargs: Keyword arguments to pass to hook

        Returns:
            True if any plugin returned True (took over execution)
            False otherwise
        """
        # Get hook execution order from config
        hook_order = self.global_config.get('hooks', {}).get(hook_name, [])

        # Track which plugins we've run
        run_plugins = set()

        # Run plugins in configured order
        for plugin_name in hook_order:
            # Try both with and without "builtin:" prefix
            plugin = self.plugins.get(plugin_name) or self.plugins.get(f"builtin:{plugin_name}")

            if plugin and plugin.is_enabled():
                run_plugins.add(plugin.name)
                result = plugin.call_hook(hook_name, *args, **kwargs)
                if result is True:
                    return True

        # Run remaining active plugins not in hook_order
        for plugin in self.get_active_plugins():
            if plugin.name not in run_plugins:
                result = plugin.call_hook(hook_name, *args, **kwargs)
                if result is True:
                    return True

        return False

    def register_all_commands(self, cli_group):
        """Allow plugins to register CLI commands.

        Args:
            cli_group: Click command group to add commands to
        """
        for plugin in self.get_active_plugins():
            plugin.call_hook('register_commands', cli_group)
