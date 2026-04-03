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


def _is_initialized_project(project_path: Path) -> bool:
    """Return True when a directory looks like an initialized cl9 project."""
    return (project_path / ".cl9" / "config.json").is_file()


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
        project_path / ".cl9" / "claude",
    ]
    planned_dirs.extend(project_path / directory for directory in env_spec.directories)

    planned_files = [
        project_path / ".cl9" / "config.json",
        project_path / ".cl9" / "env" / "state.json",
        project_path / ".cl9" / "claude" / "CLAUDE.md",
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


def _build_claude_command(project_root: Path, no_continue: bool) -> List[str]:
    """Build the Claude Code command using user config plus project-local overlays."""
    claude_dir = project_root / ".cl9" / "claude"
    cmd = ["claude", "--setting-sources", "user"]

    claude_md = claude_dir / "CLAUDE.md"
    if claude_md.is_file():
        cmd.extend(["--append-system-prompt-file", str(claude_md.resolve())])

    settings_file = claude_dir / "settings.json"
    if settings_file.is_file():
        cmd.extend(["--settings", str(settings_file.resolve())])

    mcp_file = claude_dir / "mcp.json"
    if mcp_file.is_file():
        cmd.extend(["--mcp-config", str(mcp_file.resolve())])

    if not no_continue:
        cmd.append("--continue")

    return cmd


def _collect_commands(cmd, prefix: str = "", override_name: Union[str, None] = None):
    """Collect commands recursively for auto-generated manual output."""
    results = []
    name = override_name or cmd.name
    full_name = f"{prefix} {name}".strip()
    help_text = (cmd.help or "").strip().split("\n")[0]

    if isinstance(cmd, click.Group):
        for sub_name in sorted(cmd.commands):
            sub_cmd = cmd.get_command(None, sub_name)
            if not sub_cmd:
                continue
            results.extend(_collect_commands(sub_cmd, full_name, override_name=sub_name))
        return results

    arguments = []
    options = []
    for param in cmd.params:
        if isinstance(param, click.Argument):
            arg_name = param.name.upper()
            if param.nargs != 1:
                arg_name = f"{arg_name}..."
            if param.required:
                arguments.append(arg_name)
            else:
                arguments.append(f"[{arg_name}]")
        elif isinstance(param, click.Option) and param.help:
            opts = ", ".join(param.opts)
            options.append((opts, param.help))

    results.append((full_name, help_text, arguments, options))
    return results


def _render_project_list(output_format: str) -> None:
    """Render registered projects."""
    projects = config.list_projects()

    if not projects:
        click.echo("No projects registered.")
        click.echo("Use 'cl9 project register' to add an initialized project.")
        return

    format_lower = output_format.lower()

    if format_lower == "json":
        click.echo(json.dumps(projects, indent=2))
        return

    if format_lower == "tsv":
        click.echo("NAME\tPATH\tCREATED\tLAST_ACCESSED")
        for project in projects:
            click.echo(
                f"{project['name']}\t{project['path']}\t{project['created']}\t{project.get('last_accessed', '')}"
            )
        return

    click.echo("\nRegistered cl9 Projects:\n")
    for project in projects:
        click.echo(f"**{project['name']}**")
        click.echo(f"  Path: {project['path']}")
        click.echo(f"  Created: {project['created']}")
        if project.get("last_accessed"):
            click.echo(f"  Last accessed: {project['last_accessed']}")
        if not Path(project["path"]).exists():
            click.echo("  ⚠️  Directory not found")
        click.echo()


def _register_project_path(project_path: Path) -> None:
    """Register an initialized project directory."""
    resolved_path = project_path.resolve()

    if not resolved_path.exists():
        click.echo(f"Error: Project path does not exist: {resolved_path}", err=True)
        sys.exit(1)

    if not resolved_path.is_dir():
        click.echo(f"Error: Project path is not a directory: {resolved_path}", err=True)
        sys.exit(1)

    if not _is_initialized_project(resolved_path):
        click.echo(f"Error: {resolved_path} is not an initialized cl9 project.", err=True)
        click.echo("Expected to find .cl9/config.json in that directory.", err=True)
        sys.exit(1)

    project_name = _load_local_project_name(resolved_path)
    existing_by_name = config.get_project(project_name)
    existing_by_path = config.get_project_by_path(resolved_path)

    if existing_by_name and existing_by_name["path"] == str(resolved_path):
        click.echo(f"Project '{project_name}' is already registered.")
        return

    if existing_by_path and existing_by_path["name"] != project_name:
        config.remove_project(existing_by_path["name"])

    if existing_by_name and existing_by_name["path"] != str(resolved_path):
        previous_path = Path(existing_by_name["path"])
        if _is_initialized_project(previous_path):
            click.echo(
                f"Error: Project name '{project_name}' is already registered at {existing_by_name['path']}",
                err=True,
            )
            sys.exit(1)

        config.remove_project(project_name)

    config.add_project(project_name, resolved_path)
    click.echo(f"Registered project '{project_name}'.")
    click.echo(f"  Path: {resolved_path}")


def _prune_projects() -> int:
    """Remove stale project registrations and return the number pruned."""
    pruned = 0

    for project in config.list_projects():
        project_path = Path(project["path"])
        if project_path.exists() and project_path.is_dir() and _is_initialized_project(project_path):
            continue

        config.remove_project(project["name"])
        click.echo(f"Pruned project '{project['name']}'")
        click.echo(f"  Path was: {project['path']}")
        pruned += 1

    return pruned


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

    claude_dir = cl9_dir / "claude"
    claude_dir.mkdir(parents=True)
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text(
        "\n".join(
            [
                f"# {project_name}",
                "",
                "This is a cl9-managed project.",
                "",
                "## Project Location",
                "",
                f"- Root: {project_path}",
                f"- Config: {cl9_dir}",
                "",
                "## Notes",
                "",
                "Add project-specific instructions for Claude Code agents here.",
                "",
            ]
        )
    )

    variables = build_template_variables(project_name, project_path)
    delivered_paths = apply_environment(env_spec, project_path, variables)
    delivered_files = {
        str(path.relative_to(project_path)): hash_file(path)
        for path in delivered_paths
    }
    delivered_files[str(claude_md.relative_to(project_path))] = hash_file(claude_md)
    save_state(project_path, env_type, delivered_files)

    click.echo(f"Initialized cl9 project: {project_name}")
    click.echo(f"  Location: {project_path}")
    click.echo(f"  Environment: {env_type}")
    click.echo(f"  Local state: {cl9_dir}")


def _current_project_path() -> Path:
    """Return the current project path or exit if not in a cl9 project."""
    project_path = Path.cwd()
    if _is_initialized_project(project_path):
        return project_path

    click.echo("Error: Not in a cl9 project directory.", err=True)
    click.echo("Current directory must contain .cl9/config.json.", err=True)
    click.echo("Use 'cl9 init' to initialize a project, or 'cl9 enter <target>' to enter one.", err=True)
    sys.exit(1)


def _find_project_root(start_path: Union[None, str, Path] = None) -> Union[Path, None]:
    """Walk up the directory tree to find the nearest cl9 project root."""
    current = _resolve_path(start_path or Path.cwd())

    while current != current.parent:
        if _is_initialized_project(current):
            return current
        current = current.parent

    if _is_initialized_project(current):
        return current

    return None


def _resolve_registered_project(name: str) -> dict:
    """Resolve a project from the registry."""
    project_data = config.get_project(name)
    if project_data:
        return project_data

    click.echo(f"Error: Project '{name}' not found in registry.", err=True)
    click.echo("Use 'cl9 project list' to see available projects.", err=True)
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
        click.echo("Use 'cl9 project list' to see registered projects or pass '--path' for a path.", err=True)
    sys.exit(1)


@click.group()
@click.version_option()
def main():
    """cl9 - Opinionated LLM session manager.

    Manage AI-assisted work across isolated project contexts.
    """
    pass


@main.command()
@click.pass_context
def man(ctx):
    """Print the complete manual (auto-generated from commands)."""
    root = ctx.find_root().command

    lines = ["CL9(1)", "", "NAME", "    cl9 - Opinionated LLM session manager", ""]
    groups = {}

    for cmd_name in sorted(root.list_commands(ctx)):
        if cmd_name == "man":
            continue
        cmd = root.get_command(ctx, cmd_name)
        if not cmd:
            continue
        commands = _collect_commands(cmd, override_name=cmd_name)
        if commands:
            groups[cmd_name] = commands

    lines.append("COMMANDS")

    for group_name, commands in groups.items():
        lines.append(f"\n  {group_name}:")
        for full_name, help_text, arguments, options in commands:
            args_str = " ".join(arguments)
            if args_str:
                lines.append(f"    cl9 {full_name} {args_str}")
            else:
                lines.append(f"    cl9 {full_name}")
            if help_text:
                lines.append(f"        {help_text}")
            for opt, opt_help in options:
                lines.append(f"        {opt}: {opt_help}")

    lines.extend([
        "",
        "FILES",
        "    .cl9/config.json         Project-local cl9 config",
        "    .cl9/env/state.json     Environment template state",
        "    .cl9/claude/CLAUDE.md   Project-local Claude instructions",
        "",
        "SEE ALSO",
        "    cl9 <command> --help",
    ])

    click.echo("\n".join(lines))


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

    if env_type is None:
        env_type = config.get_default_environment_type()

    if _is_initialized_project(project_path):
        existing_name = _load_local_project_name(project_path)
        if project_name and project_name != existing_name:
            click.echo(
                f"Error: Project is already initialized with name '{existing_name}'.",
                err=True,
            )
            sys.exit(1)
        click.echo(f"Project '{existing_name}' is already initialized at {project_path}.")
        return

    if project_name is None:
        project_name = _derive_project_name(project_path)

    _initialize_project(project_path, project_name, env_type)


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


@main.group(invoke_without_command=True)
@click.pass_context
def agent(ctx):
    """Agent management commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@agent.command("spawn")
@click.option("--no-continue", is_flag=True, help="Don't use --continue flag.")
def agent_spawn(no_continue):
    """Spawn a Claude Code agent in the current project."""
    project_root = _find_project_root()
    if project_root is None:
        click.echo("Error: Not inside a cl9 project.", err=True)
        click.echo("Run from within a directory containing .cl9/ (or a subdirectory).", err=True)
        sys.exit(1)

    project_name = _load_local_project_name(project_root)

    loader = get_plugin_loader()
    loader.run_hook("pre_agent", project_root)

    if loader.run_hook("on_agent", project_root):
        return

    env = os.environ.copy()
    env["CL9_PROJECT"] = project_name
    env["CL9_PROJECT_PATH"] = str(project_root)
    env["CL9_ACTIVE"] = "1"

    cmd = _build_claude_command(project_root, no_continue)

    click.echo(f"Spawning agent in project: {project_name}")
    click.echo(f"Project root: {project_root}")
    click.echo(f"Overlay: {project_root / '.cl9' / 'claude'}")
    click.echo()

    os.execvpe("claude", cmd, env)


def _write_rendered_file(dest: Path, template_file: Path, content: bytes) -> None:
    """Write rendered template content and preserve mode bits."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    os.chmod(dest, template_file.stat().st_mode)


@main.command("update")
@click.option("--diff", is_flag=True, help="Show what would change without modifying files.")
@click.option("--force", is_flag=True, help="Overwrite user-modified files.")
def update(diff, force):
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


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False))
def completion(shell):
    """Output shell completion script for the specified shell."""
    _emit_completion_script(shell)


@main.group()
def project():
    """Project registry management commands."""
    pass


@project.command("register")
@click.argument("path", required=False, default=".")
def project_register(path):
    """Register an initialized project in the global registry."""
    _register_project_path(_resolve_path(path))


@project.command("list")
@click.option(
    "-f",
    "--format",
    type=click.Choice(["markdown", "md", "json", "tsv"], case_sensitive=False),
    default="markdown",
    help="Output format (default: markdown)",
)
def project_list(format):
    """List registered projects."""
    _render_project_list(format)


@project.command("remove")
@click.argument("project_name", shell_complete=complete_project_names)
def project_remove(project_name):
    """Remove a project from the registry."""
    project_data = config.get_project(project_name)

    if not project_data:
        click.echo(f"Error: Project '{project_name}' not found in registry.", err=True)
        click.echo("Use 'cl9 project list' to see registered projects.", err=True)
        sys.exit(1)

    config.remove_project(project_name)
    click.echo(f"Removed project '{project_name}' from registry.")
    click.echo(f"  Path was: {project_data['path']}")
    click.echo()
    click.echo("Note: Project files and .cl9 directory were not deleted.")


@project.command("prune")
def project_prune():
    """Remove registrations for projects whose directories are gone or invalid."""
    pruned = _prune_projects()
    if pruned == 0:
        click.echo("No stale project registrations found.")
        return

    click.echo()
    click.echo(f"Pruned {pruned} project registrations.")


if __name__ == '__main__':
    main()
