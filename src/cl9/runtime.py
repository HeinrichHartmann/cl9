"""Runtime-directory helpers for the ADR 0009 spawn pipeline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .profiles import ProfileSpec

_SKIP_FILES = {"manifest.json", "settings.json", "mcp.json"}


def runtime_dir_for(project_root: Path, session_id: str) -> Path:
    """Return the runtime directory path for a session."""
    return project_root / ".cl9" / "sessions" / session_id / "runtime"


def materialize_profile_into_runtime(profile: ProfileSpec, runtime_dir: Path) -> None:
    """Raw-copy non-config files from the profile into the runtime directory.

    Skips manifest.json, settings.json, and mcp.json — cl9 reads those
    directly into cl9.agent state and writes them in write_agent_config.
    """
    for src in sorted(profile.path.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(profile.path)
        if rel.parent == Path(".") and src.name in _SKIP_FILES:
            continue
        dest = runtime_dir / src.relative_to(profile.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        shutil.copymode(src, dest)


def _resolve_runtime_vars(obj: object, runtime_dir: Path) -> object:
    """Replace ${CL9_RUNTIME_DIR} with the actual path in nested structures."""
    runtime_str = str(runtime_dir)
    if isinstance(obj, str):
        return obj.replace("${CL9_RUNTIME_DIR}", runtime_str)
    if isinstance(obj, dict):
        return {k: _resolve_runtime_vars(v, runtime_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_runtime_vars(v, runtime_dir) for v in obj]
    return obj


def write_agent_config(runtime_dir: Path) -> None:
    """Serialize cl9.agent.settings and cl9.agent.mcp into the runtime directory."""
    import cl9.agent as agent

    if agent.settings:
        resolved = _resolve_runtime_vars(agent.settings, runtime_dir)
        (runtime_dir / "settings.json").write_text(
            json.dumps(resolved, indent=2)
        )
    if agent.mcp:
        (runtime_dir / "mcp.json").write_text(
            json.dumps(agent.mcp, indent=2)
        )


def remove_runtime(project_root: Path, session_id: str) -> None:
    """Remove the runtime directory and its session parent if empty. Idempotent."""
    runtime_dir = runtime_dir_for(project_root, session_id)
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)

    session_dir = runtime_dir.parent
    if session_dir.exists():
        try:
            session_dir.rmdir()  # only removes if empty
        except OSError:
            pass
