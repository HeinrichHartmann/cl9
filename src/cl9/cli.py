"""CLI commands for cl9."""

import os
import sys
import json
from pathlib import Path
import click
from click.shell_completion import CompletionItem

from .config import config


def complete_project_names(ctx, param, incomplete):
    """Completion function for project names."""
    projects = config.list_projects()
    return [
        CompletionItem(p['name'], help=p['path'])
        for p in projects
        if p['name'].startswith(incomplete)
    ]


@click.group()
@click.version_option()
def main():
    """cl9 - Opinionated LLM session manager.

    Manage AI-assisted work across isolated project contexts.
    """
    pass


@main.command()
@click.argument('project_name', required=False)
def init(project_name):
    """Initialize a cl9 project in the current directory.

    If PROJECT_NAME is not provided, uses the directory name.
    """
    current_dir = Path.cwd()

    # Determine project name
    if project_name is None:
        project_name = current_dir.name

    # Check if project already exists
    if config.project_exists(project_name):
        existing = config.get_project(project_name)
        if existing['path'] == str(current_dir):
            click.echo(f"Project '{project_name}' is already initialized in this directory.")
            return
        else:
            click.echo(
                f"Error: Project name '{project_name}' already exists at {existing['path']}",
                err=True
            )
            sys.exit(1)

    # Create .cl9 directory
    cl9_dir = current_dir / ".cl9"
    cl9_dir.mkdir(exist_ok=True)

    # Create basic project config
    project_config = {
        "name": project_name,
        "version": "1",
    }

    config_file = cl9_dir / "config.json"
    with open(config_file, 'w') as f:
        json.dump(project_config, f, indent=2)

    # Add to global registry
    config.add_project(project_name, current_dir)

    click.echo(f"Initialized cl9 project: {project_name}")
    click.echo(f"  Location: {current_dir}")
    click.echo(f"  Local state: {cl9_dir}")


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
                click.echo(f"  ⚠️  Directory not found")
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
@click.argument('project', shell_complete=complete_project_names)
def enter(project):
    """Enter a project context by spawning a subshell in its directory.

    Spawns a new shell session in the project directory. Use 'exit' or Ctrl+D
    to leave the project context and return to your original shell.
    """
    # Get project from registry
    project_data = config.get_project(project)

    if not project_data:
        click.echo(f"Error: Project '{project}' not found.", err=True)
        click.echo("Use 'cl9 list' to see available projects.", err=True)
        sys.exit(1)

    project_path = Path(project_data['path'])

    # Check if directory exists
    if not project_path.exists():
        click.echo(f"Error: Project directory does not exist: {project_path}", err=True)
        sys.exit(1)

    # Check if .cl9 directory exists
    cl9_dir = project_path / ".cl9"
    if not cl9_dir.exists():
        click.echo(f"Error: Project is not initialized (missing .cl9 directory)", err=True)
        click.echo(f"Run 'cl9 init' in {project_path}", err=True)
        sys.exit(1)

    # Update last accessed timestamp
    config.update_last_accessed(project)

    click.echo(f"Entering project: {project}")
    click.echo(f"Location: {project_path}")
    click.echo(f"Type 'exit' or press Ctrl+D to leave project context")
    click.echo()

    # Change to project directory
    os.chdir(project_path)

    # Set up cl9 environment variables
    env = os.environ.copy()
    env['CL9_PROJECT'] = project
    env['CL9_PROJECT_PATH'] = str(project_path)
    env['CL9_ACTIVE'] = '1'

    # Get shell from environment, fallback to /bin/sh
    shell = env.get('SHELL', '/bin/sh')

    # Spawn subshell
    os.execvpe(shell, [shell], env)


@main.command()
def agent():
    """Launch an LLM agent in the current project.

    Must be run from within a cl9 project directory (one with a .cl9/ subdirectory).
    Launches 'claude --continue' in the current directory.
    """
    current_dir = Path.cwd()
    cl9_dir = current_dir / ".cl9"

    # Check if we're in a cl9 project
    if not cl9_dir.exists():
        click.echo("Error: Not in a cl9 project directory.", err=True)
        click.echo("Current directory must contain a .cl9/ subdirectory.", err=True)
        click.echo("Use 'cl9 init' to initialize a project, or 'cl9 enter <project>' to enter one.", err=True)
        sys.exit(1)

    # Execute claude --continue
    # Using os.execvp to replace the current process
    os.execvp("claude", ["claude", "--continue"])


@main.command()
@click.argument('shell', type=click.Choice(['bash', 'zsh', 'fish'], case_sensitive=False))
def env(shell):
    """Output shell completion script for the specified shell.

    Usage:
        source <(cl9 env zsh)     # zsh
        source <(cl9 env bash)    # bash
        cl9 env fish | source     # fish
    """
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

    click.echo(script.strip())


if __name__ == '__main__':
    main()
