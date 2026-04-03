"""Environment template management."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional


BUILTIN_DIR = Path(__file__).parent
BUILTIN_ENVIRONMENT_DIRECTORIES = {
    "default": ["src", "doc", "data"],
    "minimal": [],
}


@dataclass(frozen=True)
class EnvironmentSpec:
    """Resolved environment type definition."""

    type_name: str
    template_path: Optional[Path]
    directories: List[str]


def get_environment_path(type_name: str, user_env_dir: Path) -> Optional[Path]:
    """Resolve an environment template directory for file-backed types."""
    builtin = BUILTIN_DIR / type_name
    if builtin.is_dir():
        return builtin

    user = user_env_dir / type_name
    if user.is_dir():
        return user

    local = Path(type_name).expanduser()
    if local.is_dir():
        return local.resolve()

    return None


def get_environment_directories(type_name: str) -> List[str]:
    """Return directories that should exist for the environment type."""
    return list(BUILTIN_ENVIRONMENT_DIRECTORIES.get(type_name, []))


def resolve_environment(type_name: str, user_env_dir: Path) -> Optional[EnvironmentSpec]:
    """Resolve an environment type to template files and managed directories."""
    if type_name in BUILTIN_ENVIRONMENT_DIRECTORIES:
        return EnvironmentSpec(
            type_name=type_name,
            template_path=get_environment_path(type_name, user_env_dir),
            directories=get_environment_directories(type_name),
        )

    env_path = get_environment_path(type_name, user_env_dir)
    if env_path is None:
        return None

    return EnvironmentSpec(type_name=type_name, template_path=env_path, directories=[])


def list_builtin_types() -> List[str]:
    """List available built-in environment types."""
    return sorted(BUILTIN_ENVIRONMENT_DIRECTORIES)


def build_template_variables(project_name: str, project_path: Path) -> Dict[str, str]:
    """Build the variable substitution map for templates."""
    return {
        "PROJECT_NAME": project_name,
        "PROJECT_PATH": str(project_path.resolve()),
        "DATE": date.today().isoformat(),
    }


def iter_template_files(env_path: Optional[Path]) -> List[Path]:
    """Return the files provided by an environment template."""
    if env_path is None:
        return []

    return sorted(
        [path for path in env_path.rglob("*") if path.is_file()],
        key=lambda path: str(path.relative_to(env_path)),
    )


def render_template_file(path: Path, variables: Dict[str, str]) -> bytes:
    """Render a template file with variable substitution when it is text."""
    try:
        content = path.read_text()
    except UnicodeDecodeError:
        return path.read_bytes()

    for var, value in variables.items():
        content = content.replace(f"{{{{{var}}}}}", value)
    return content.encode()


def apply_environment(spec: EnvironmentSpec, target_path: Path, variables: Dict[str, str]) -> List[Path]:
    """Apply an environment template to the target directory."""
    delivered_files: List[Path] = []

    for directory in spec.directories:
        (target_path / directory).mkdir(parents=True, exist_ok=True)

    for template_file in iter_template_files(spec.template_path):
        rel_path = template_file.relative_to(spec.template_path)
        dest = target_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(render_template_file(template_file, variables))
        shutil.copymode(template_file, dest)
        delivered_files.append(dest)

    return delivered_files


def hash_bytes(content: bytes) -> str:
    """Compute a SHA256 hash for bytes."""
    return "sha256:" + hashlib.sha256(content).hexdigest()


def hash_file(path: Path) -> str:
    """Compute a SHA256 hash of a file."""
    return hash_bytes(path.read_bytes())


def save_state(project_path: Path, env_type: str, files: Dict[str, str]) -> None:
    """Save environment state to .cl9/env/state.json."""
    state_dir = project_path / ".cl9" / "env"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "type": env_type,
        "version": "1",
        "applied_at": datetime.now().isoformat(),
        "files": files,
    }
    (state_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n")


def load_state(project_path: Path) -> Optional[dict]:
    """Load environment state from .cl9/env/state.json."""
    state_file = project_path / ".cl9" / "env" / "state.json"
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text())
