"""CLI commands for cl9."""

import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Tuple, Union

import click
from click.shell_completion import CompletionItem

from .adapters import get_adapter_for_profile
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
from .profiles import ProfileSpec, builtin_profile, resolve_profile
from .sessions import ProjectState


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
    project_config = _load_local_project_config(project_path)
    project_name = project_config.get("name")
    if not project_name:
        config_file = project_path / ".cl9" / "config.json"
        click.echo(
            f"Error: Project config at {config_file} is missing a 'name' field.",
            err=True,
        )
        sys.exit(1)

    return project_name


def _load_local_project_config(project_path: Path) -> dict:
    """Load project metadata from local .cl9 configuration."""
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
    return project_config


def _is_initialized_project(project_path: Path) -> bool:
    """Return True when a directory looks like an initialized cl9 project."""
    return (project_path / ".cl9" / "config.json").is_file()


def _project_state(project_path: Path) -> ProjectState:
    """Return the project-local state manager."""
    return ProjectState(project_path)


def _default_profile_name(project_path: Path) -> str:
    """Return the configured default profile name."""
    project_config = _load_local_project_config(project_path)
    return project_config.get("default_profile", "default")


def _project_profiles_dir(project_path: Path) -> Path:
    """Return the project-local profiles directory."""
    return project_path / ".cl9" / "profiles"


def _project_context_env(project_path: Path, project_name: str) -> dict:
    """Build the shared project execution environment."""
    env = os.environ.copy()
    project_bin = project_path / "bin"
    env["PATH"] = f"{project_bin}:{env.get('PATH', '')}" if env.get("PATH") else str(project_bin)
    env["CL9_PROJECT"] = project_name
    env["CL9_PROJECT_PATH"] = str(project_path)
    env["CL9_ACTIVE"] = "1"
    return env


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
        project_path / ".cl9" / "profiles",
        project_path / ".cl9" / "profiles" / "default",
    ]
    planned_dirs.extend(project_path / directory for directory in env_spec.directories)

    planned_files = [
        project_path / ".cl9" / "config.json",
        project_path / ".cl9" / "env" / "state.json",
        project_path / ".cl9" / "profiles" / "default" / "CLAUDE.md",
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


def _render_default_profile_files(project_path: Path, project_name: str) -> List[Tuple[str, bytes, Path]]:
    """Render the default project-local profile scaffold."""
    default_profile = builtin_profile("default")
    if default_profile is None:
        raise click.ClickException("Built-in default profile is missing.")

    variables = build_template_variables(project_name, project_path)
    rendered_files: List[Tuple[str, bytes, Path]] = []
    for profile_file in sorted(default_profile.path.rglob("*")):
        if not profile_file.is_file():
            continue
        rel_path = Path(".cl9") / "profiles" / "default" / profile_file.relative_to(default_profile.path)
        rendered_files.append((str(rel_path), render_template_file(profile_file, variables), profile_file))
    return rendered_files


def _materialize_profile(project_root: Path, profile_name: str) -> Path:
    """Ensure a profile has a project-local working copy and return its path."""
    local_dir = _project_profiles_dir(project_root) / profile_name
    if local_dir.is_dir():
        return local_dir.resolve()

    resolved = resolve_profile(profile_name, project_root, config.profiles_dir)
    if resolved is None or resolved.path.resolve() == local_dir.resolve():
        raise click.ClickException(f"Profile '{profile_name}' was not found.")

    variables = build_template_variables(_load_local_project_name(project_root), project_root)
    local_dir.mkdir(parents=True, exist_ok=True)
    for source_file in sorted(resolved.path.rglob("*")):
        if not source_file.is_file():
            continue
        dest = local_dir / source_file.relative_to(resolved.path)
        _write_rendered_file(dest, source_file, render_template_file(source_file, variables))

    return local_dir.resolve()


def _desired_project_files(project_path: Path, project_name: str, env_type: str) -> Tuple[object, List[Tuple[str, bytes, Path]]]:
    """Return the environment spec and rendered managed files for a project."""
    env_spec = _resolve_environment_spec(env_type)
    variables = build_template_variables(project_name, project_path)
    desired_files: List[Tuple[str, bytes, Path]] = []

    for template_file in iter_template_files(env_spec.template_path):
        rel_path = str(template_file.relative_to(env_spec.template_path))
        desired_files.append((rel_path, render_template_file(template_file, variables), template_file))

    desired_files.extend(_render_default_profile_files(project_path, project_name))
    return env_spec, desired_files


def _sync_project_files(project_path: Path, project_name: str, env_type: str, diff: bool, force: bool) -> bool:
    """Preview or apply the managed project files."""
    env_spec, desired_files = _desired_project_files(project_path, project_name, env_type)
    changed = False

    click.echo("Dry run - no files will be modified" if diff else f"Applying template: {env_type}")
    click.echo()

    for directory in env_spec.directories:
        dest_dir = project_path / directory
        if dest_dir.exists():
            continue
        click.echo(f"  {'Would add' if diff else 'Added'} dir:  {directory}/")
        if not diff:
            dest_dir.mkdir(parents=True, exist_ok=True)
        changed = True

    (_project_profiles_dir(project_path) / "default").mkdir(parents=True, exist_ok=True) if not diff else None

    delivered_files = {}
    for rel_path, rendered, source_path in desired_files:
        dest = project_path / rel_path
        rendered_hash = hash_bytes(rendered)
        current_hash = hash_file(dest) if dest.exists() and dest.is_file() else None

        if current_hash == rendered_hash:
            delivered_files[rel_path] = rendered_hash
            continue

        label = None
        if not dest.exists():
            label = "Would add" if diff else "Added"
        elif diff and not force:
            label = "Would clobber"
        elif diff and force:
            label = "Would overwrite"
        elif force:
            label = "Overwrote"
        else:
            label = "Would clobber"

        click.echo(f"  {label}:   {rel_path}")
        changed = True

        if not diff:
            _write_rendered_file(dest, source_path, rendered)
            delivered_files[rel_path] = rendered_hash

    if not diff:
        save_state(project_path, env_type, delivered_files)

    if not changed:
        click.echo("Project is already up to date.")

    return changed


def _resolve_agent_profile(project_root: Path, profile_name: str) -> ProfileSpec:
    """Materialize and resolve a profile, returning the ProfileSpec."""
    _materialize_profile(project_root, profile_name)
    profile = resolve_profile(profile_name, project_root, config.profiles_dir)
    if profile is None:
        raise click.ClickException(f"Profile '{profile_name}' was not found.")
    return profile


def _shell_executable(env: dict) -> str:
    """Return the preferred shell executable."""
    return env.get("SHELL", os.environ.get("SHELL", "/bin/sh"))


def _spawn_in_project_shell(cwd: Path, env: dict, argv: List[str]) -> subprocess.Popen:
    """Spawn a command inside the user's shell with project context."""
    shell = _shell_executable(env)
    script = f"cd {shlex.quote(str(cwd.resolve()))} && exec {shlex.join(argv)}"
    return subprocess.Popen([shell, "-ic", script], env=env)


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

    variables = build_template_variables(project_name, project_path)
    delivered_paths = apply_environment(env_spec, project_path, variables)
    delivered_files = {
        str(path.relative_to(project_path)): hash_file(path)
        for path in delivered_paths
    }

    profiles_dir = _project_profiles_dir(project_path) / "default"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, rendered, source_path in _render_default_profile_files(project_path, project_name):
        dest = project_path / rel_path
        _write_rendered_file(dest, source_path, rendered)
        delivered_files[rel_path] = hash_bytes(rendered)

    save_state(project_path, env_type, delivered_files)

    click.echo(f"Initialized cl9 project: {project_name}")
    click.echo(f"  Location: {project_path}")
    click.echo(f"  Environment: {env_type}")
    click.echo(f"  Local state: {cl9_dir}")


def _current_project_path() -> Path:
    """Return the current project path or exit if not in a cl9 project."""
    project_path = _find_project_root()
    if project_path is not None:
        return project_path

    click.echo("Error: Not in a cl9 project directory.", err=True)
    click.echo("Current directory must be inside a directory containing .cl9/config.json.", err=True)
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

    if not _is_initialized_project(project_path):
        click.echo(
            f"Error: Project at {project_path} is not initialized (missing .cl9/config.json).",
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
        "    .cl9/profiles/default/CLAUDE.md   Project-local default agent profile",
        "    .cl9/state.db           Project-local session/process state",
        "",
        "SEE ALSO",
        "    cl9 <command> --help",
        "",
        "AUTHOR",
        "    This tool is provided to you by Heinrich Hartmann under the MIT license.",
        "    The core repository is at https://github.com/HeinrichHartmann/cl9",
        "    PRs are open. Don't hesitate to open issues for feature requests or bugs",
        "    you encounter.",
    ])

    click.echo("\n".join(lines))


def _init_command(path: str, project_name: Union[str, None], env_type: Union[str, None], force: bool) -> None:
    """Implementation for init commands and aliases."""
    project_path = _resolve_path(path)

    if not project_path.exists():
        click.echo(f"Error: Project path does not exist: {project_path}", err=True)
        sys.exit(1)

    if not project_path.is_dir():
        click.echo(f"Error: Project path is not a directory: {project_path}", err=True)
        sys.exit(1)

    existing_project = _is_initialized_project(project_path)
    if existing_project:
        existing_name = _load_local_project_name(project_path)
        if project_name and project_name != existing_name:
            click.echo(
                f"Error: Project is already initialized with name '{existing_name}'.",
                err=True,
            )
            sys.exit(1)
        project_name = existing_name
    elif project_name is None:
        project_name = _derive_project_name(project_path)

    if env_type is None:
        state = load_state(project_path) if existing_project else None
        env_type = state["type"] if state else config.get_default_environment_type()

    if existing_project and not force:
        _sync_project_files(project_path, project_name, env_type, diff=True, force=False)
        click.echo()
        click.echo("Run 'cl9 init --force' to apply these changes.")
        return

    if existing_project:
        _sync_project_files(project_path, project_name, env_type, diff=False, force=True)
        click.echo()
        click.echo(f"Reinitialized cl9 project: {project_name}")
        click.echo(f"  Location: {project_path}")
        click.echo(f"  Environment: {env_type}")
        return

    _initialize_project(project_path, project_name, env_type)


def _enter_command(target: str, force_name: bool, force_path: bool) -> None:
    """Implementation for enter commands and aliases."""
    project_data = _resolve_enter_target(target, force_name, force_path)

    loader = get_plugin_loader()
    loader.run_hook("pre_enter", project_data)

    project_path = Path(project_data["path"])
    if not project_path.exists():
        click.echo(f"Error: Project directory does not exist: {project_path}", err=True)
        sys.exit(1)

    if not _is_initialized_project(project_path):
        click.echo("Error: Project is not initialized (missing .cl9/config.json)", err=True)
        click.echo(f"Run 'cl9 init' in {project_path}", err=True)
        sys.exit(1)

    registered_project = config.get_project(project_data["name"])
    if registered_project and registered_project["path"] == str(project_path):
        config.update_last_accessed(project_data["name"])

    env = _project_context_env(project_path, project_data["name"])

    if loader.run_hook("on_enter", project_data, env):
        return

    click.echo(f"Entering project: {project_data['name']}")
    click.echo(f"Location: {project_path}")
    click.echo("Type 'exit' or press Ctrl+D to leave project context")
    click.echo()

    os.chdir(project_path)
    shell = _shell_executable(env)
    os.execvpe(shell, [shell], env)


def _run_project_command(command_argv: Tuple[str, ...]) -> None:
    """Run a command in the current project's shell environment."""
    project_path = _current_project_path()
    project_name = _load_local_project_name(project_path)
    env = _project_context_env(project_path, project_name)

    if not command_argv:
        raise click.UsageError("COMMAND is required.")

    process = _spawn_in_project_shell(project_path, env, list(command_argv))
    sys.exit(process.wait())


@main.command()
@click.argument("path", required=False, default=".")
@click.option("-n", "--name", "project_name", help="Explicit project name.")
@click.option("-t", "--type", "env_type", help="Environment type (default: configured default or 'default').")
@click.option("--force", is_flag=True, help="Re-apply the template to an initialized project.")
def init(path, project_name, env_type, force):
    """Initialize a cl9 project in a directory.

    PATH defaults to the current directory. If --name is not provided, the
    directory name is used.
    """
    _init_command(path, project_name, env_type, force)


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
    _enter_command(target, force_name, force_path)


def _launch_agent_process(
    project_root: Path,
    session_id: str,
    session_name: Union[str, None],
    profile: ProfileSpec,
    cmd: List[str],
) -> None:
    """Launch an agent process and update project-local session state."""
    project_name = _load_local_project_name(project_root)
    env = _project_context_env(project_root, project_name)
    env["CL9_SESSION_ID"] = session_id
    env["CL9_PROFILE"] = profile.name
    env["CL9_TOOL"] = profile.tool
    if session_name:
        env["CL9_SESSION_NAME"] = session_name

    state = _project_state(project_root)
    current_cwd = Path.cwd().resolve()
    process_id = state.start_process(session_id, current_cwd, cmd)

    click.echo(f"Launching agent in project: {project_name}")
    click.echo(f"Project root: {project_root}")
    click.echo(f"Session: {session_id}")
    if session_name:
        click.echo(f"Name: {session_name}")
    click.echo(f"Profile: {profile.name}")
    click.echo(f"Tool: {profile.tool}")
    click.echo()

    try:
        process = _spawn_in_project_shell(current_cwd, env, cmd)
        state.mark_process_running(process_id, process.pid)
        exit_code = process.wait()
    except OSError as exc:
        state.fail_process_start(process_id, session_id)
        raise click.ClickException(str(exc)) from exc

    state.finish_process(process_id, session_id, exit_code)
    if exit_code != 0:
        sys.exit(exit_code)


@main.group(invoke_without_command=True)
@click.pass_context
def agent(ctx):
    """Agent management commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@agent.command("spawn")
@click.option("--name", "session_name", help="Optional session name.")
@click.option("-p", "--profile", "profile_name", help="Agent profile name.")
@click.argument("agent_args", nargs=-1, type=click.UNPROCESSED)
def agent_spawn(session_name, profile_name, agent_args):
    """Spawn an agent in the current project.

    The agent tool (Claude Code, Codex, etc.) is determined by the profile's
    manifest. Use --profile to select a different profile than the default.
    """
    project_root = _find_project_root()
    if project_root is None:
        click.echo("Error: Not inside a cl9 project.", err=True)
        click.echo("Run from within a directory containing .cl9/ (or a subdirectory).", err=True)
        sys.exit(1)

    loader = get_plugin_loader()
    loader.run_hook("pre_agent", project_root)

    if loader.run_hook("on_agent", project_root):
        return

    resolved_profile_name = profile_name or _default_profile_name(project_root)
    profile = _resolve_agent_profile(project_root, resolved_profile_name)
    adapter = get_adapter_for_profile(profile)

    session_id = str(uuid.uuid4())
    launch_spec = adapter.build_spawn_command(profile, session_id, list(agent_args))

    state = _project_state(project_root)
    state.create_session(
        session_id,
        session_name,
        resolved_profile_name,
        profile.tool,
        Path.cwd(),
    )

    _launch_agent_process(
        project_root,
        session_id,
        session_name,
        profile,
        launch_spec.command,
    )


@agent.command("continue")
@click.argument("target", required=False)
@click.argument("agent_args", nargs=-1, type=click.UNPROCESSED)
def agent_continue(target, agent_args):
    """Resume an existing session."""
    project_root = _current_project_path()
    state = _project_state(project_root)
    try:
        session = state.resolve_session_target(target)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if state.session_has_running_process(session.session_id):
        raise click.ClickException("Session already has a running process.")

    profile = _resolve_agent_profile(project_root, session.profile)
    adapter = get_adapter_for_profile(profile)

    # For tools like Codex, we need the tool_session_id from session metadata
    tool_session_id = session.metadata.get("tool_session_id") if session.metadata else None
    launch_spec = adapter.build_continue_command(
        profile, session.session_id, tool_session_id, list(agent_args)
    )

    _launch_agent_process(
        project_root,
        session.session_id,
        session.name,
        profile,
        launch_spec.command,
    )


@agent.command("fork")
@click.argument("target")
@click.option("--name", "session_name", help="Optional session name.")
@click.option("-p", "--profile", "profile_name", help="Agent profile name.")
@click.argument("agent_args", nargs=-1, type=click.UNPROCESSED)
def agent_fork(target, session_name, profile_name, agent_args):
    """Fork an existing session into a new session."""
    project_root = _current_project_path()
    state = _project_state(project_root)
    try:
        parent = state.resolve_session_target(target)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    resolved_profile_name = profile_name or parent.profile
    profile = _resolve_agent_profile(project_root, resolved_profile_name)
    adapter = get_adapter_for_profile(profile)

    child_session_id = str(uuid.uuid4())

    # For tools like Codex, we need the parent's tool_session_id
    parent_tool_session_id = parent.metadata.get("tool_session_id") if parent.metadata else None
    launch_spec = adapter.build_fork_command(
        profile, parent.session_id, child_session_id, parent_tool_session_id, list(agent_args)
    )

    state.create_session(
        child_session_id,
        session_name,
        resolved_profile_name,
        profile.tool,
        Path.cwd(),
        forked_from_session_id=parent.session_id,
    )

    _launch_agent_process(
        project_root,
        child_session_id,
        session_name,
        profile,
        launch_spec.command,
    )


def _write_rendered_file(dest: Path, template_file: Path, content: bytes) -> None:
    """Write rendered template content and preserve mode bits."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    os.chmod(dest, template_file.stat().st_mode)


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False))
def completion(shell):
    """Output shell completion script for the specified shell."""
    _emit_completion_script(shell)


@main.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("command_argv", nargs=-1, type=click.UNPROCESSED)
def run_alias(command_argv):
    """Run a command in the current project environment."""
    _run_project_command(command_argv)


@main.group()
def project():
    """Project management commands."""
    pass


@project.command("init")
@click.argument("path", required=False, default=".")
@click.option("-n", "--name", "project_name", help="Explicit project name.")
@click.option("-t", "--type", "env_type", help="Environment type (default: configured default or 'default').")
@click.option("--force", is_flag=True, help="Re-apply the template to an initialized project.")
def project_init(path, project_name, env_type, force):
    """Initialize a project directory."""
    _init_command(path, project_name, env_type, force)


@project.command("enter")
@click.argument("target", shell_complete=complete_project_names)
@click.option("-n", "--name", "force_name", is_flag=True, help="Interpret TARGET as a project name.")
@click.option("-p", "--path", "force_path", is_flag=True, help="Interpret TARGET as a filesystem path.")
def project_enter(target, force_name, force_path):
    """Enter a project context."""
    _enter_command(target, force_name, force_path)


@project.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("command_argv", nargs=-1, type=click.UNPROCESSED)
def project_run(command_argv):
    """Run a command in the project environment."""
    _run_project_command(command_argv)


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


@main.group()
def session():
    """Project-local session management commands."""
    pass


@session.command("list")
def session_list():
    """List sessions for the current project."""
    project_root = _current_project_path()
    sessions = _project_state(project_root).list_sessions()

    if not sessions:
        click.echo("No sessions found.")
        return

    for entry in sessions:
        display = entry["name"] or entry["session_id"]
        click.echo(f"{display}")
        click.echo(f"  ID: {entry['session_id']}")
        click.echo(f"  Profile: {entry['profile']}")
        click.echo(f"  Status: {entry['status']}")
        click.echo(f"  Last used: {entry['last_used_at']}")
        if entry["forked_from_session_id"]:
            click.echo(f"  Forked from: {entry['forked_from_session_id']}")
        if entry["has_running_process"]:
            click.echo("  Running: yes")
        click.echo()


@session.command("prune")
@click.option("--older-than", default="30d", help="Prune idle sessions older than this age (for example 30d).")
def session_prune(older_than):
    """Remove old idle sessions from local tracking."""
    value = older_than.strip().lower()
    if not value.endswith("d") or not value[:-1].isdigit():
        raise click.UsageError("--older-than currently expects a whole number of days, e.g. 30d")

    project_root = _current_project_path()
    pruned = _project_state(project_root).prune_sessions(int(value[:-1]))
    if pruned == 0:
        click.echo("No sessions pruned.")
        return
    click.echo(f"Pruned {pruned} sessions.")


@session.command("delete")
@click.argument("target")
@click.option("--force", is_flag=True, help="Delete local tracking even if the session looks active.")
def session_delete(target, force):
    """Delete a session from local tracking."""
    project_root = _current_project_path()
    state = _project_state(project_root)
    try:
        session_target = state.resolve_session_target(target)
        state.delete_session(session_target.session_id, force=force)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Deleted local tracking for session {session_target.session_id}.")


if __name__ == '__main__':
    main()
