"""Agent profile resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .mounts import MOUNTS_DIR

BUILTIN_DIR = Path(__file__).parent / "profiles"
USER_PROFILES_DIR = Path.home() / ".cl9" / "profiles"

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
    def isolation(self) -> str:
        """Return the isolation mode: 'compose' (default) or 'full'."""
        return self.manifest.get("isolation", "compose")

    @property
    def instructions_md(self) -> Path:
        """Return the profile's instructions file (for Codex-style agents)."""
        return self.path / "INSTRUCTIONS.md"


def builtin_profile(name: str) -> Optional[ProfileSpec]:
    """Resolve a built-in profile by name."""
    candidate = BUILTIN_DIR / name
    if candidate.is_dir():
        resolved_path = candidate.resolve()
        manifest = _load_manifest(resolved_path)
        return ProfileSpec(name=name, path=resolved_path, manifest=manifest)
    return None


def user_profile(name: str) -> Optional[ProfileSpec]:
    """Resolve a user-local profile from ~/.cl9/profiles/<name>."""
    candidate = USER_PROFILES_DIR / name
    if candidate.is_dir():
        resolved_path = candidate.resolve()
        manifest = _load_manifest(resolved_path)
        return ProfileSpec(name=name, path=resolved_path, manifest=manifest)
    return None


def _iter_mount_profile_dirs():
    """Yield (mount_name, profile_dir) for every profile in every mount."""
    if not MOUNTS_DIR.is_dir():
        return
    for mount in sorted(MOUNTS_DIR.iterdir()):
        if not mount.is_dir():
            continue
        profiles_dir = mount / "profiles"
        if not profiles_dir.is_dir():
            continue
        for profile_dir in sorted(profiles_dir.iterdir()):
            if profile_dir.is_dir():
                yield mount.name, profile_dir


def mounted_profile(name: str) -> Optional[Tuple[ProfileSpec, str]]:
    """Resolve a profile from any mount. Returns (spec, mount_name) or None.

    If multiple mounts define the same profile name, the first one wins
    (mounts are iterated in sorted order by mount directory name).
    """
    for mount_name, profile_dir in _iter_mount_profile_dirs():
        if profile_dir.name == name:
            resolved_path = profile_dir.resolve()
            manifest = _load_manifest(resolved_path)
            return (
                ProfileSpec(name=name, path=resolved_path, manifest=manifest),
                mount_name,
            )
    return None


def resolve_profile(name: str) -> Optional[ProfileSpec]:
    """Resolve a profile by name.

    Precedence: user-local → mounted → built-in.
    """
    user = user_profile(name)
    if user:
        return user
    mounted = mounted_profile(name)
    if mounted:
        return mounted[0]
    return builtin_profile(name)


def list_profiles() -> List[Tuple[ProfileSpec, str]]:
    """Return all available profiles as (ProfileSpec, source) pairs.

    Precedence is user → mount:<name> → builtin; earlier sources shadow later
    ones. The source string is 'user', 'mount:<mount-name>', or 'builtin'.
    """
    seen: Dict[str, Tuple[ProfileSpec, str]] = {}

    if USER_PROFILES_DIR.is_dir():
        for candidate in sorted(USER_PROFILES_DIR.iterdir()):
            if candidate.is_dir():
                name = candidate.name
                manifest = _load_manifest(candidate.resolve())
                seen[name] = (
                    ProfileSpec(name=name, path=candidate.resolve(), manifest=manifest),
                    "user",
                )

    for mount_name, profile_dir in _iter_mount_profile_dirs():
        name = profile_dir.name
        if name not in seen:
            manifest = _load_manifest(profile_dir.resolve())
            seen[name] = (
                ProfileSpec(name=name, path=profile_dir.resolve(), manifest=manifest),
                f"mount:{mount_name}",
            )

    for candidate in sorted(BUILTIN_DIR.iterdir()):
        if candidate.is_dir():
            name = candidate.name
            if name not in seen:
                manifest = _load_manifest(candidate.resolve())
                seen[name] = (
                    ProfileSpec(name=name, path=candidate.resolve(), manifest=manifest),
                    "builtin",
                )

    return list(seen.values())
