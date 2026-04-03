"""CLI commands for cl9."""

import json
import os
import sys
from pathlib import Path
from typing import List, Tuple, Union

import click
from click.shell_completion import CompletionItem

from .config import config
from .environments import (
    apply_environment,
    build_template_variables,
    hash_bytes,
    hash_file,
    iter_template_files,
    load_state,
    render_template_file,
    resolve_environment,
    save_state,
)
from .plugins import PluginLoader


# Global plugin loader (initialized once)
_plugin_loader = None


def get_plugin_loader() -> PluginLoader:
    """Get or initialize the global plugin loader."""
    global _plugin_loader
    if _plugin_loader is None:
        global_config = config.load_global_config()
        _plugin_loader = PluginLoader(global_config)
        _plugin_loader.load_all(config.plugins_dir)
    return _plugin_loader


def complete_project_names(ctx, param, incomplete):
    """Completion function for project names."""
    projects = config.list_projects()
    return [
        CompletionItem(p['name'], help=p['path'])
        for p in projects
        if p['name'].startswith(incomplete)
    ]


def _resolve_path(path_value: Union[str, Path]) -> Path:
    """Resolve a filesystem path, including ``~`` expansion."""
    return Path(path_value).expanduser().resolve()


def _derive_project_name(project_path: Path) -> str:
    """Derive the default project name from a directory path."""
    return project_path.name or project_path.anchor.rstrip(os.sep) or project_path.anchor


def _load_local_project_name(project_path: Path) -> str:
    """Load a project name from local .cl9 configuration."""
    config_file = project_path / ".cl9" / "config.json"

    if not config_file.exists():
        click.echo(
            f"Error: Project config not found at {config_file}",
            err=True,
        )
        sys.exit(1)

    try:
        with open(config_file, "r") as f:
            project_config = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        click.echo(
            f"Error: Failed to read project config at {config_file}: {exc}",
            err=True,
        )
        sys.exit(1)

    project_name = project_config.get("name")
    if not project_name:
        click.echo(
            f"Error: Project config at {config_file} is missing a 'name' field.",
            err=True,
        )
        sys.exit(1)

    return project_name


def _emit_completion_script(shell: str) -> None:
    """Print the shell completion script for the specified shell."""
    shell_lower = shell.lower()

    if shell_lower == 'bash':
        script = '''
# cl9 bash completion
eval "$(_CL9_COMPLETE=bash_source cl9)"
'''
    elif shell_lower == 'zsh':
        script = '''
# cl9 zsh completion
eval "$(_CL9_COMPLETE=zsh_source cl9)"
'''
    elif shell_lower == 'fish':
        script = '''
# cl9 fish completion
eval (env _CL9_COMPLETE=fish_source cl9)
'''
    else:
        raise click.UsageError(f"Unsupported shell: {shell}")

    click.echo(script.strip())


def _resolve_environment_spec(env_type: str):
    """Resolve an environment type or exit with a helpful error."""
    env_spec = resolve_environment(env_type, config.environments_dir)
    if env_spec:
        return env_spec

    click.echo(f"Error: Environment type '{env_type}' was not found.", err=True)
    click.echo(
        "Check built-in types, ~/.config/cl9/environments/, or pass a local template path.",
        err=True,
    )
    sys.exit(1)


def _planned_environment_paths(project_path: Path, env_spec) -> Tuple[List[Path], List[Path]]:
    """Return the directories and files initialization will create."""
    planned_dirs = [
        project_path / ".cl9",
        project_path / ".cl9" / "env",
    ]
    planned_dirs.extend(project_path / directory for directory in env_spec.directories)

    planned_files = [
        project_path / ".cl9" / "config.json",
        project_path / ".cl9" / "env" / "state.json",
    ]

    for template_file in iter_template_files(env_spec.template_path):
        planned_files.append(project_path / template_file.relative_to(env_spec.template_path))

    return planned_dirs, planned_files


def _fail_on_init_conflicts(project_path: Path, env_spec) -> None:
    """Abort init if the target directory already contains conflicting paths."""
    planned_dirs, planned_files = _planned_environment_paths(project_path, env_spec)
    conflicts = [path for path in planned_dirs + planned_files if path.exists()]

    if not conflicts:
        return

    click.echo("Error: Cannot initialize because these paths already exist:", err=True)
    for conflict in sorted(conflicts):
        rel_path = conflict.relative_to(project_path)
        click.echo(f"  {rel_path}", err=True)
    click.echo("Move them out of the way and run 'cl9 init' again.", err=True)
    sys.exit(1)


def _initialize_project(project_path: Path, project_name: str, env_type: str) -> None:
    """Initialize project files, environment scaffolding, and registry state."""
    env_spec = _resolve_environment_spec(env_type)
    _fail_on_init_conflicts(project_path, env_spec)

    cl9_dir = project_path / ".cl9"
    cl9_dir.mkdir(parents=True)

    project_config = {
        "name": project_name,
        "version": "1",
    }

    config_file = cl9_dir / "config.json"
    with open(config_file, "w") as f:
        json.dump(project_config, f, indent=2)

    variables = build_template_variables(project_name, project_path)
    delivered_paths = apply_environment(env_spec, project_path, variables)
    delivered_files = {
        str(path.relative_to(project_path)): hash_file(path)
        for path in delivered_paths
    }
    save_state(project_path, env_type, delivered_files)

    config.add_project(project_name, project_path)

    click.echo(f"Initialized cl9 project: {project_name}")
    click.echo(f"  Location: {project_path}")
    click.echo(f"  Environment: {env_type}")
    click.echo(f"  Local state: {cl9_dir}")


def _current_project_path() -> Path:
    """Return the current project path or exit if not in a cl9 project."""
    project_path = Path.cwd()
    cl9_dir = project_path / ".cl9"

    if cl9_dir.exists():
        return project_path

    click.echo("Error: Not in a cl9 project directory.", err=True)
    click.echo("Current directory must contain a .cl9/ subdirectory.", err=True)
    click.echo("Use 'cl9 init' to initialize a project, or 'cl9 enter <target>' to enter one.", err=True)
    sys.exit(1)


def _resolve_registered_project(name: str) -> dict:
    """Resolve a project from the registry."""
    project_data = config.get_project(name)
    if project_data:
        return project_data

    click.echo(f"Error: Project '{name}' not found in registry.", err=True)
    click.echo("Use 'cl9 list' to see available projects.", err=True)
    sys.exit(1)


def _resolve_path_project(target: str) -> dict:
    """Resolve a project from a filesystem path."""
    project_path = _resolve_path(target)

    if not project_path.exists():
        click.echo(f"Error: Project path does not exist: {project_path}", err=True)
        sys.exit(1)

    if not project_path.is_dir():
        click.echo(f"Error: Project path is not a directory: {project_path}", err=True)
        sys.exit(1)

    cl9_dir = project_path / ".cl9"
    if not cl9_dir.is_dir():
        click.echo(
            f"Error: Project at {project_path} is not initialized (missing .cl9 directory).",
            err=True,
        )
        click.echo(f"Run 'cl9 init {project_path}' to initialize it.", err=True)
        sys.exit(1)

    project_name = _load_local_project_name(project_path)
    project_data = {
        "name": project_name,
        "path": str(project_path),
        "created": None,
        "last_accessed": None,
    }

    registered_project = config.get_project(project_name)
    if registered_project and registered_project["path"] == str(project_path):
        project_data.update(registered_project)

    return project_data


def _resolve_enter_target(target: str, force_name: bool, force_path: bool) -> dict:
    """Resolve an enter target as either a registry name or filesystem path."""
    if force_name and force_path:
        raise click.UsageError("Options '--name' and '--path' are mutually exclusive.")

    if force_name:
        return _resolve_registered_project(target)

    if force_path:
        return _resolve_path_project(target)

    project_data = config.get_project(target)
    if project_data:
        return project_data

    project_path = _resolve_path(target)
    if project_path.is_dir() and (project_path / ".cl9").is_dir():
        return _resolve_path_project(target)

    click.echo(
        f"Error: Could not resolve '{target}' as a registered project name or initialized project path.",
        err=True,
    )
    if project_path.exists() and project_path.is_dir():
        click.echo(f"Path exists but is missing .cl9/: {project_path}", err=True)
    else:
        click.echo("Use 'cl9 list' to see registered projects or pass '--path' for a path.", err=True)
    sys.exit(1)


@click.group()
@click.version_option()
def main():
    """cl9 - Opinionated LLM session manager.

    Manage AI-assisted work across isolated project contexts.
    """
    pass


@main.command()
@click.argument("path", required=False, default=".")
@click.option("-n", "--name", "project_name", help="Explicit project name.")
@click.option("-t", "--type", "env_type", help="Environment type (default: configured default or 'default').")
def init(path, project_name, env_type):
    """Initialize a cl9 project in a directory.

    PATH defaults to the current directory. If --name is not provided, the
    directory name is used.
    """
    project_path = _resolve_path(path)

    if not project_path.exists():
        click.echo(f"Error: Project path does not exist: {project_path}", err=True)
        sys.exit(1)

    if not project_path.is_dir():
        click.echo(f"Error: Project path is not a directory: {project_path}", err=True)
        sys.exit(1)

    if project_name is None:
        project_name = _derive_project_name(project_path)

    if env_type is None:
        env_type = config.get_default_environment_type()

    # Check if project already exists
    if config.project_exists(project_name):
        existing = config.get_project(project_name)
        if existing["path"] == str(project_path):
            click.echo(f"Project '{project_name}' is already initialized at {project_path}.")
            return

        click.echo(
            f"Error: Project name '{project_name}' already exists at {existing['path']}",
            err=True,
        )
        sys.exit(1)

    _initialize_project(project_path, project_name, env_type)


@main.command()
@click.option(
    '-f', '--format',
    type=click.Choice(['markdown', 'md', 'json', 'tsv'], case_sensitive=False),
    default='markdown',
    help='Output format (default: markdown)'
)
def list(format):
    """List all registered cl9 projects."""
    projects = config.list_projects()

    if not projects:
        click.echo("No projects registered.")
        click.echo("Use 'cl9 init' to register a project.")
        return

    format_lower = format.lower()

    if format_lower == 'json':
        click.echo(json.dumps(projects, indent=2))
    elif format_lower == 'tsv':
        # TSV output: name, path, created, last_accessed
        click.echo("NAME\tPATH\tCREATED\tLAST_ACCESSED")
        for p in projects:
            click.echo(f"{p['name']}\t{p['path']}\t{p['created']}\t{p.get('last_accessed', '')}")
    else:  # markdown
        click.echo("\nRegistered cl9 Projects:\n")
        for p in projects:
            click.echo(f"**{p['name']}**")
            click.echo(f"  Path: {p['path']}")
            click.echo(f"  Created: {p['created']}")
            if p.get('last_accessed'):
                click.echo(f"  Last accessed: {p['last_accessed']}")
            # Check if directory exists
            if not Path(p['path']).exists():
                click.echo("  ⚠️  Directory not found")
            click.echo()


@main.command()
@click.argument('project', shell_complete=complete_project_names)
def remove(project):
    """Remove a project from the registry.

    This only removes the project from cl9's registry, it does not delete
    any files or directories.
    """
    # Check if project exists
    if not config.project_exists(project):
        click.echo(f"Error: Project '{project}' not found in registry.", err=True)
        click.echo("Use 'cl9 list' to see registered projects.", err=True)
        sys.exit(1)

    # Get project details before removing
    project_data = config.get_project(project)

    # Remove from registry
    if config.remove_project(project):
        click.echo(f"Removed project '{project}' from registry.")
        click.echo(f"  Path was: {project_data['path']}")
        click.echo()
        click.echo("Note: Project files and .cl9 directory were not deleted.")
    else:
        click.echo(f"Error: Failed to remove project '{project}'.", err=True)
        sys.exit(1)


@main.command()
@click.argument("target", shell_complete=complete_project_names)
@click.option("-n", "--name", "force_name", is_flag=True, help="Interpret TARGET as a project name.")
@click.option("-p", "--path", "force_path", is_flag=True, help="Interpret TARGET as a filesystem path.")
def enter(target, force_name, force_path):
    """Enter a project context by spawning a subshell in its directory.

    Spawns a new shell session in the project directory. Use 'exit' or Ctrl+D
    to leave the project context and return to your original shell.

    If tmux integration is enabled and you're in a tmux session, creates
    a split-pane window instead of spawning a subshell.
    """
    project_data = _resolve_enter_target(target, force_name, force_path)

    # Get plugin loader
    loader = get_plugin_loader()

    # Run pre_enter hooks (observation only)
    loader.run_hook('pre_enter', project_data)

    # Validate project
    project_path = Path(project_data['path'])

    # Check if directory exists
    if not project_path.exists():
        click.echo(f"Error: Project directory does not exist: {project_path}", err=True)
        sys.exit(1)

    # Check if .cl9 directory exists
    cl9_dir = project_path / ".cl9"
    if not cl9_dir.exists():
        click.echo("Error: Project is not initialized (missing .cl9 directory)", err=True)
        click.echo(f"Run 'cl9 init' in {project_path}", err=True)
        sys.exit(1)

    # Update last accessed timestamp when entering by a registered name.
    registered_project = config.get_project(project_data["name"])
    if registered_project and registered_project["path"] == str(project_path):
        config.update_last_accessed(project_data["name"])

    # Set up cl9 environment variables
    env = os.environ.copy()
    env['CL9_PROJECT'] = project_data["name"]
    env['CL9_PROJECT_PATH'] = str(project_path)
    env['CL9_ACTIVE'] = '1'

    # Run on_enter hook - plugin may take over (e.g., tmux)
    if loader.run_hook('on_enter', project_data, env):
        # Plugin handled enter (e.g., created tmux window)
        # Don't spawn subshell, just exit
        return

    # Default behavior: spawn subshell
    click.echo(f"Entering project: {project_data['name']}")
    click.echo(f"Location: {project_path}")
    click.echo("Type 'exit' or press Ctrl+D to leave project context")
    click.echo()

    # Change to project directory
    os.chdir(project_path)

    # Get shell from environment, fallback to /bin/sh
    shell = env.get('SHELL', '/bin/sh')

    # Spawn subshell (this replaces current process)
    os.execvpe(shell, [shell], env)


@main.command()
def agent():
    """Launch an LLM agent in the current project.

    Must be run from within a cl9 project directory (one with a .cl9/ subdirectory).
    Launches 'claude --continue' in the current directory.
    """
    current_dir = _current_project_path()

    # Get plugin loader
    loader = get_plugin_loader()

    # Run pre_agent hooks
    loader.run_hook('pre_agent', current_dir)

    # Run on_agent hook - plugin may take over
    if loader.run_hook('on_agent', current_dir):
        # Plugin handled agent launch
        return

    # Default behavior: Execute claude --continue
    # Using os.execvp to replace the current process
    os.execvp("claude", ["claude", "--continue"])


@main.group()
def env():
    """Environment and shell integration commands."""
    pass


@env.command("init")
@click.argument("path", required=False, default=".")
@click.option("-n", "--name", "project_name", help="Explicit project name.")
@click.option("-t", "--type", "env_type", help="Environment type (default: configured default or 'default').")
@click.pass_context
def env_init(ctx, path, project_name, env_type):
    """Initialize a cl9 project (alias for 'cl9 init')."""
    ctx.invoke(init, path=path, project_name=project_name, env_type=env_type)


def _write_rendered_file(dest: Path, template_file: Path, content: bytes) -> None:
    """Write rendered template content and preserve mode bits."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    os.chmod(dest, template_file.stat().st_mode)


@env.command("update")
@click.option("--diff", is_flag=True, help="Show what would change without modifying files.")
@click.option("--force", is_flag=True, help="Overwrite user-modified files.")
def env_update(diff, force):
    """Update the current project's environment from its template."""
    project_path = _current_project_path()
    project_name = _load_local_project_name(project_path)
    state = load_state(project_path)

    if state is None:
        click.echo("Error: Project was not initialized with environment tracking.", err=True)
        click.echo("Reinitialize the project environment or add .cl9/env/state.json.", err=True)
        sys.exit(1)

    env_type = state["type"]
    env_spec = _resolve_environment_spec(env_type)
    variables = build_template_variables(project_name, project_path)
    tracked_files = dict(state.get("files", {}))
    added_count = 0
    updated_count = 0
    skipped_count = 0

    if diff:
        click.echo("Dry run - no files will be modified")
    elif force:
        click.echo(f"Updating environment (type: {env_type}) [FORCE]")
    else:
        click.echo(f"Updating environment (type: {env_type})")
    click.echo()

    for directory in env_spec.directories:
        dest_dir = project_path / directory
        if dest_dir.exists():
            continue
        if diff:
            click.echo(f"  Would add dir:  {directory}/")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            click.echo(f"  Added dir:      {directory}/")
        added_count += 1

    for template_file in iter_template_files(env_spec.template_path):
        rel_path = str(template_file.relative_to(env_spec.template_path))
        dest = project_path / rel_path
        rendered = render_template_file(template_file, variables)
        rendered_hash = hash_bytes(rendered)
        current_hash = hash_file(dest) if dest.exists() and dest.is_file() else None
        tracked_hash = tracked_files.get(rel_path)

        if dest.exists() and not dest.is_file():
            label = "Would skip" if diff else "Skipped"
            click.echo(f"  {label}:       {rel_path} (path is a directory)")
            skipped_count += 1
            continue

        if tracked_hash is None:
            if dest.exists() and not force:
                label = "Would skip" if diff else "Skipped"
                click.echo(f"  {label}:       {rel_path} (existing untracked file)")
                skipped_count += 1
                continue

            label = "Would add" if diff else "Added"
            click.echo(f"  {label}:        {rel_path}")
            if not diff:
                _write_rendered_file(dest, template_file, rendered)
                tracked_files[rel_path] = rendered_hash
            added_count += 1
            continue

        if current_hash is None:
            label = "Would add" if diff else "Added"
            click.echo(f"  {label}:        {rel_path}")
            if not diff:
                _write_rendered_file(dest, template_file, rendered)
                tracked_files[rel_path] = rendered_hash
            added_count += 1
            continue

        if current_hash != tracked_hash and not force:
            label = "Would skip" if diff else "Skipped"
            click.echo(f"  {label}:       {rel_path} (modified by user)")
            skipped_count += 1
            continue

        if current_hash == rendered_hash and current_hash == tracked_hash:
            tracked_files[rel_path] = tracked_hash
            continue

        label = "Would update" if diff else "Updated"
        suffix = " (was modified)" if current_hash != tracked_hash else ""
        click.echo(f"  {label}:     {rel_path}{suffix}")
        if not diff:
            _write_rendered_file(dest, template_file, rendered)
            tracked_files[rel_path] = rendered_hash
        updated_count += 1

    if not diff:
        save_state(project_path, env_type, tracked_files)

    if added_count == 0 and updated_count == 0 and skipped_count == 0:
        click.echo("Environment is already up to date.")
        return

    click.echo()
    if diff:
        click.echo(f"{updated_count} updates, {added_count} additions, {skipped_count} skips")
    else:
        click.echo(f"{updated_count} updated, {added_count} added, {skipped_count} skipped")


@env.command("bash")
def env_bash():
    """Output shell completion for Bash."""
    _emit_completion_script("bash")


@env.command("zsh")
def env_zsh():
    """Output shell completion for Zsh."""
    _emit_completion_script("zsh")


@env.command("fish")
def env_fish():
    """Output shell completion for Fish."""
    _emit_completion_script("fish")


if __name__ == '__main__':
    main()
