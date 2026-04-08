"""Agent profile resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


BUILTIN_DIR = Path(__file__).parent / "profiles"

DEFAULT_MANIFEST: Dict[str, Any] = {
    "tool": "claude",
    "executable": "claude",
}


def _load_manifest(profile_path: Path) -> Dict[str, Any]:
    """Load manifest.json from a profile directory, with defaults."""
    manifest_file = profile_path / "manifest.json"
    if not manifest_file.is_file():
        return DEFAULT_MANIFEST.copy()
    try:
        with open(manifest_file) as f:
            data = json.load(f)
        result = DEFAULT_MANIFEST.copy()
        result.update(data)
        return result
    except (json.JSONDecodeError, OSError):
        return DEFAULT_MANIFEST.copy()


@dataclass(frozen=True)
class ProfileSpec:
    """Resolved profile definition."""

    name: str
    path: Path
    manifest: Dict[str, Any] = field(default_factory=dict)

    @property
    def tool(self) -> str:
        """Return the tool identifier (e.g., 'claude', 'codex')."""
        return self.manifest.get("tool", "claude")

    @property
    def executable(self) -> str:
        """Return the executable name to invoke."""
        return self.manifest.get("executable", "claude")

    @property
    def claude_md(self) -> Path:
        """Return the profile's CLAUDE.md path."""
        return self.path / "CLAUDE.md"

    @property
    def settings_json(self) -> Path:
        """Return the profile's optional settings path."""
        return self.path / "settings.json"

    @property
    def mcp_json(self) -> Path:
        """Return the profile's optional MCP config path."""
        return self.path / "mcp.json"

    @property
    def instructions_md(self) -> Path:
        """Return the profile's instructions file (for Codex-style agents)."""
        return self.path / "INSTRUCTIONS.md"


def builtin_profile(name: str) -> Optional[ProfileSpec]:
    """Resolve a built-in profile by name only."""
    candidate = BUILTIN_DIR / name
    if candidate.is_dir():
        resolved_path = candidate.resolve()
        manifest = _load_manifest(resolved_path)
        return ProfileSpec(name=name, path=resolved_path, manifest=manifest)
    return None
