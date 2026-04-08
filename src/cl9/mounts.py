"""External profile/skill/mcp sources mounted under ~/.cl9/mounts/.

A mount is a git clone of a repository that may contain any of:
- profiles/<name>/  — agent profiles
- mcps/<name>/      — MCP server definitions (future)
- skills/<name>/    — skills (future)

The name "mount" follows the Plan 9 vocabulary: mounting attaches an external
namespace (a git repository) into cl9's view. See README §Naming & Inspiration.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

MOUNTS_DIR = Path.home() / ".cl9" / "mounts"


@dataclass(frozen=True)
class MountInfo:
    """A mounted external source."""

    name: str
    path: Path
    origin: Optional[str]
    ref: Optional[str]
    profile_count: int
    mcp_count: int
    skill_count: int


def parse_mount_spec(spec: str) -> Tuple[str, Optional[str]]:
    """Split a mount spec into (repo, ref).

    The syntax is ``<repo>[@<ref>]``, following the Go modules / pip
    convention. We split on the last ``@`` in the spec, and only treat
    its trailing segment as a ref if that segment does not contain
    ``:``. SSH URLs use ``user@host:path``, so an ``@`` whose tail
    contains ``:`` belongs to the URL, not a ref. Git branch and tag
    names cannot contain ``:``, so this is sufficient to disambiguate.

    Examples:
        parse_mount_spec("/tmp/repo")                      -> ("/tmp/repo", None)
        parse_mount_spec("/tmp/repo@main")                 -> ("/tmp/repo", "main")
        parse_mount_spec("git@github.com:foo/bar")         -> ("git@github.com:foo/bar", None)
        parse_mount_spec("git@github.com:foo/bar@v1.2.3")  -> ("git@github.com:foo/bar", "v1.2.3")
        parse_mount_spec("https://github.com/foo/bar@abc") -> ("https://github.com/foo/bar", "abc")
    """
    if "@" not in spec:
        return spec, None
    head, _, tail = spec.rpartition("@")
    # If the tail contains ':', it's part of an SSH URL, not a ref.
    if ":" in tail:
        return spec, None
    # Guard against pathological leading '@': "@foo" has no repo.
    if not head:
        return spec, None
    return head, tail or None


def infer_mount_name(git_url: str) -> str:
    """Derive a mount directory name from a git URL or filesystem path.

    Any trailing ``@<ref>`` is stripped first.
    """
    url, _ = parse_mount_spec(git_url)
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if "/" in url:
        url = url.rsplit("/", 1)[-1]
    elif ":" in url:
        url = url.rsplit(":", 1)[-1]
    return url or "mount"


def _validate_mount_name(name: str) -> None:
    """Reject mount names that could escape MOUNTS_DIR or surprise the user.

    Mount names become a single directory entry under ``~/.cl9/mounts/``.
    Any path separator, parent reference, or hidden-file prefix is rejected
    so that ``cl9 mount add ... --name <evil>`` cannot write outside the
    mounts directory.
    """
    if not name:
        raise ValueError("Mount name must not be empty.")
    if name in (".", ".."):
        raise ValueError(f"Mount name '{name}' is not allowed.")
    if name.startswith("."):
        raise ValueError(f"Mount name '{name}' must not start with '.'.")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"Mount name '{name}' must not contain path separators.")


def _run_git(args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run a git command, capturing output. Raises on non-zero exit."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def _get_config(path: Path, key: str) -> Optional[str]:
    """Return a git config value, or None if unset or on failure."""
    try:
        result = _run_git(["config", "--get", key], cwd=path)
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_origin(path: Path) -> Optional[str]:
    """Return the origin remote URL of a git clone, or None on failure."""
    return _get_config(path, "remote.origin.url")


def _get_mount_ref(path: Path) -> Optional[str]:
    """Return the pinned ref recorded for a mount, or None if unpinned."""
    return _get_config(path, "cl9.mountRef")


def _count_subdirs(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for child in path.iterdir() if child.is_dir())


def mount_info(name: str) -> Optional[MountInfo]:
    """Return info about a single mount, or None if it does not exist."""
    mount_dir = MOUNTS_DIR / name
    if not mount_dir.is_dir():
        return None
    return MountInfo(
        name=name,
        path=mount_dir,
        origin=_get_origin(mount_dir),
        ref=_get_mount_ref(mount_dir),
        profile_count=_count_subdirs(mount_dir / "profiles"),
        mcp_count=_count_subdirs(mount_dir / "mcps"),
        skill_count=_count_subdirs(mount_dir / "skills"),
    )


def list_mounts() -> List[MountInfo]:
    """List all mounted sources."""
    if not MOUNTS_DIR.is_dir():
        return []
    mounts = []
    for entry in sorted(MOUNTS_DIR.iterdir()):
        if entry.is_dir():
            info = mount_info(entry.name)
            if info:
                mounts.append(info)
    return mounts


def add_mount(spec: str, name: Optional[str] = None) -> MountInfo:
    """Clone a git repository into ~/.cl9/mounts/<name>/.

    ``spec`` is a mount spec in the form ``<repo>[@<ref>]``. See
    :func:`parse_mount_spec` for parsing rules. When a ref is given, the
    clone is performed at full depth (so that any tree-ish — branch, tag,
    or commit SHA — can be checked out), and the ref is recorded in the
    clone's git config as ``cl9.mountRef`` for later ``update`` calls.

    Raises ValueError if the target name already exists.
    Raises RuntimeError if git clone or checkout fails.
    """
    repo, ref = parse_mount_spec(spec)
    mount_name = name if name is not None else infer_mount_name(repo)
    _validate_mount_name(mount_name)
    dest = MOUNTS_DIR / mount_name

    if dest.exists():
        raise ValueError(f"Mount '{mount_name}' already exists at {dest}.")

    MOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if ref is None:
            # No ref: shallow clone of the default branch. Fast path.
            _run_git(["clone", "--depth", "1", repo, str(dest)])
        else:
            # Ref given: full clone so any tree-ish (SHA, tag, branch) can
            # be checked out. Then pin the ref in git config so update()
            # knows what to fetch against.
            _run_git(["clone", repo, str(dest)])
            _run_git(["checkout", ref], cwd=dest)
            _run_git(["config", "cl9.mountRef", ref], cwd=dest)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        # Leave no half-cloned directory behind.
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {stderr or exc}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH.") from exc

    info = mount_info(mount_name)
    assert info is not None  # just cloned
    return info


def update_mount(name: str) -> MountInfo:
    """Refresh a mount, discarding any local state.

    Mounts are treated as read-only extensions of cl9, not working copies.
    Update fetches from origin and performs a hard reset + clean, so any
    local modifications or stale files are dropped. There is no merge, no
    rebase, no preservation of divergent history.

    When the mount is unpinned (no ``cl9.mountRef`` set), update fetches
    the remote HEAD shallowly and resets to it. When the mount is pinned,
    update fetches all of origin and resets to the pinned ref — so a
    branch pin follows the branch, a tag pin follows the tag (and is a
    no-op if the tag has not moved), and a SHA pin is idempotent.
    """
    info = mount_info(name)
    if info is None:
        raise ValueError(f"Mount '{name}' not found.")

    try:
        if info.ref is None:
            _run_git(["fetch", "--depth", "1", "origin"], cwd=info.path)
            _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=info.path)
        else:
            # ``--tags --force`` ensures a tag that has been moved upstream
            # actually moves locally; without it, git keeps the old tag.
            _run_git(["fetch", "--tags", "--force", "origin"], cwd=info.path)
            # Prefer origin/<ref> if it exists (branch case); fall back to
            # <ref> itself (tag or SHA).
            try:
                _run_git(["rev-parse", "--verify", f"origin/{info.ref}"], cwd=info.path)
                target = f"origin/{info.ref}"
            except subprocess.CalledProcessError:
                target = info.ref
            _run_git(["reset", "--hard", target], cwd=info.path)
        _run_git(["clean", "-fdx"], cwd=info.path)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"git update failed for '{name}': {stderr or exc}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH.") from exc

    refreshed = mount_info(name)
    assert refreshed is not None
    return refreshed


def remove_mount(name: str) -> None:
    """Delete a mount directory. Raises ValueError if not found."""
    _validate_mount_name(name)
    mount_dir = MOUNTS_DIR / name
    if not mount_dir.is_dir():
        raise ValueError(f"Mount '{name}' not found.")
    shutil.rmtree(mount_dir)
