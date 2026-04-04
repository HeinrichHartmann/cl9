"""Agent profile resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


BUILTIN_DIR = Path(__file__).parent / "profiles"


@dataclass(frozen=True)
class ProfileSpec:
    """Resolved profile definition."""

    name: str
    path: Path

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


def resolve_profile(name: str, project_root: Path, user_profiles_dir: Path) -> Optional[ProfileSpec]:
    """Resolve a profile by name."""
    candidates = [
        project_root / ".cl9" / "profiles" / name,
        user_profiles_dir / name,
        BUILTIN_DIR / name,
    ]

    for candidate in candidates:
        if candidate.is_dir():
            return ProfileSpec(name=name, path=candidate.resolve())

    return None


def builtin_profile(name: str) -> Optional[ProfileSpec]:
    """Resolve a built-in profile by name only."""
    candidate = BUILTIN_DIR / name
    if candidate.is_dir():
        return ProfileSpec(name=name, path=candidate.resolve())
    return None
