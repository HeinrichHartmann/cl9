"""Public surface for cl9 init scripts.

Init scripts import this module and mutate the three dicts below.
cl9 reads them back after init returns to build the agent's runtime.

Example::

    from cl9 import agent
    agent.env["ANTHROPIC_API_KEY"] = "sk-..."

cl9 calls ``_reset`` before running each init script to clear state
from any previous invocation. Do not call ``_reset`` from init scripts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Mutable agent configuration — init.py writes here
# ---------------------------------------------------------------------------

env: dict = {}       # forwarded into the agent process env at launch
settings: dict = {}  # serialized to <runtime>/settings.json
mcp: dict = {}       # serialized to <runtime>/mcp.json

# ---------------------------------------------------------------------------
# Read-only session context — cl9 sets these before init runs
# ---------------------------------------------------------------------------

project_root: Optional[Path] = None
profile_name: Optional[str] = None
profile_dir: Optional[Path] = None
runtime_dir: Optional[Path] = None
session_id: Optional[str] = None
session_name: Optional[str] = None


def _reset(
    *,
    project_root: Path,
    profile_name: str,
    profile_dir: Path,
    runtime_dir: Path,
    session_id: str,
    session_name: Optional[str],
    settings_baseline: dict,
    mcp_baseline: dict,
) -> None:
    """Called by cl9 before running init.py. Not part of the init-script API."""
    import cl9.agent as _m  # reference the module itself to assign globals

    _m.env = {}
    _m.settings = dict(settings_baseline)
    _m.mcp = dict(mcp_baseline)

    _m.project_root = project_root
    _m.profile_name = profile_name
    _m.profile_dir = profile_dir
    _m.runtime_dir = runtime_dir
    _m.session_id = session_id
    _m.session_name = session_name
