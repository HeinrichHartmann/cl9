"""Tmux integration plugin for cl9.

Creates and manages tmux windows for cl9 projects with split-pane layout.
"""

import os
import shutil
import subprocess


# Define PluginLoadError locally to avoid import issues with dynamic loading
class PluginLoadError(Exception):
    """Raised when a plugin fails to load."""
    pass


# Plugin metadata
PLUGIN_NAME = "tmux"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Tmux window management integration"

# Hard-coded constants (no config for now)
WINDOW_PREFIX = "cl9:"
SPLIT_ORIENTATION = "horizontal"  # Top/bottom
TOP_PANE_PERCENTAGE = 75
BOTTOM_PANE_PERCENTAGE = 25
INITIAL_FOCUS_PANE = "top"  # "top" or "bottom"
REUSE_EXISTING_WINDOW = True

# Default config (currently not used, but required for future compatibility)
DEFAULT_CONFIG = {
    "enabled": True
}


# Plugin initialization - runs when module is loaded
def _check_tmux_available():
    """Check if tmux is available on PATH.

    Raises:
        PluginLoadError: If tmux is not found
    """
    if not shutil.which('tmux'):
        raise PluginLoadError("tmux executable not found on PATH")


# Run check on module load
_check_tmux_available()


def is_enabled(config: dict) -> bool:
    """Check if plugin should be active.

    Plugin is enabled if:
    1. Config says enabled=True (or not specified, defaults to True)
    2. Currently in a tmux session (TMUX env var exists)

    Args:
        config: Plugin configuration from global config

    Returns:
        True if plugin is active
    """
    if not config.get('enabled', True):
        return False

    # Only active if in tmux session
    return os.environ.get('TMUX') is not None


def on_enter(project_data: dict, env: dict, config: dict) -> bool:
    """Hook: Handle project entry with tmux window management.

    Creates or switches to a tmux window for the project with split layout.

    Args:
        project_data: Project info (name, path, created, last_accessed)
        env: Environment variables dict (CL9_PROJECT, CL9_PROJECT_PATH, etc.)
        config: Plugin configuration

    Returns:
        True (takes over execution - no subshell spawn needed)
    """
    project_name = project_data['name']
    project_path = project_data['path']

    window_name = f"{WINDOW_PREFIX}{project_name}"

    # Check if window exists and reuse if configured
    if REUSE_EXISTING_WINDOW and _window_exists(window_name):
        _switch_to_window(window_name)
    else:
        _create_window_with_split(window_name, project_path, env)

    # Return True to indicate we handled the enter
    # (CLI should not spawn subshell)
    return True


# Helper functions

def _window_exists(window_name: str) -> bool:
    """Check if a tmux window with given name exists in current session.

    Args:
        window_name: Window name to check

    Returns:
        True if window exists in current session
    """
    try:
        result = subprocess.run(
            ['tmux', 'list-windows', '-F', '#{window_name}'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        windows = result.stdout.strip().split('\n')
        return window_name in windows
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"Warning: Failed to list tmux windows: {e}")
        return False


def _switch_to_window(window_name: str):
    """Switch to existing tmux window.

    Args:
        window_name: Name of window to switch to
    """
    try:
        subprocess.run(
            ['tmux', 'select-window', '-t', window_name],
            check=True,
            timeout=5
        )
        print(f"Switched to existing window: {window_name}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"Error: Failed to switch to window {window_name}: {e}")


def _create_window_with_split(window_name: str, project_path: str, env: dict):
    """Create new tmux window with split layout.

    Layout:
    ┌─────────────────────────────┐
    │  Top Pane (75%)             │
    │  Shell in project dir       │
    ├─────────────────────────────┤
    │  Bottom Pane (25%)          │
    │  Shell in project dir       │
    └─────────────────────────────┘

    Args:
        window_name: Name for the new window
        project_path: Path to project directory
        env: Environment variables to set
    """
    try:
        # Create new window in current session
        subprocess.run(
            ['tmux', 'new-window', '-n', window_name, '-c', project_path],
            check=True,
            timeout=5
        )

        # Split window horizontally (creates bottom pane)
        # The -p flag specifies percentage for the NEW pane (bottom)
        subprocess.run(
            ['tmux', 'split-window', '-v', '-p', str(BOTTOM_PANE_PERCENTAGE), '-c', project_path],
            check=True,
            timeout=5
        )

        # Set environment variables in the window
        # Note: tmux set-environment affects the session, not individual panes
        # We rely on the shell inheriting CL9_* vars from the parent process

        # Focus on the desired pane
        pane_index = '0' if INITIAL_FOCUS_PANE == 'top' else '1'
        subprocess.run(
            ['tmux', 'select-pane', '-t', pane_index],
            check=True,
            timeout=5
        )

        print(f"Created tmux window: {window_name}")
        print(f"  Top pane ({TOP_PANE_PERCENTAGE}%): shell in {project_path}")
        print(f"  Bottom pane ({BOTTOM_PANE_PERCENTAGE}%): shell in {project_path}")
        print(f"  Focus: {INITIAL_FOCUS_PANE} pane")
        print()
        print("Use 'exit' or close the window to leave project context.")

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"Error: Failed to create tmux window: {e}")
        # Don't crash - let caller decide what to do
        raise
