"""Launch adapters for different agent tools."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Type

from .profiles import ProfileSpec


# ── Keychain helpers (macOS only) ─────────────────────────────────────────────

_KEYCHAIN_SOURCE = "Claude Code-credentials"


def _keychain_service_for(config_dir: Path) -> str:
    """Return the keychain service name Claude Code uses for a given CLAUDE_CONFIG_DIR."""
    h = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    return f"{_KEYCHAIN_SOURCE}-{h}"


def copy_keychain_credential(config_dir: Path) -> None:
    """Copy the Claude Code OAuth credential to the hashed entry for config_dir.

    Called at spawn time when isolation='full'. Safe to call on non-macOS
    (no-op) and safe to call when no source credential exists (no-op).
    """
    if platform.system() != "Darwin":
        return
    result = subprocess.run(
        ["security", "find-generic-password", "-s", _KEYCHAIN_SOURCE, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return  # No credential to copy
    token_data = result.stdout.strip()
    target = _keychain_service_for(config_dir)
    # Delete stale entry if present, then add fresh copy
    subprocess.run(
        ["security", "delete-generic-password", "-s", target],
        capture_output=True,
    )
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", target,
         "-a", _get_username(), "-w", token_data],
        capture_output=True, check=True,
    )


def delete_keychain_credential(config_dir: Path) -> None:
    """Remove the hashed keychain entry for config_dir. Called on runtime cleanup."""
    if platform.system() != "Darwin":
        return
    target = _keychain_service_for(config_dir)
    subprocess.run(
        ["security", "delete-generic-password", "-s", target],
        capture_output=True,
    )


def _get_username() -> str:
    try:
        import os
        return os.environ.get("USER") or __import__("os").getlogin()
    except Exception:
        return "claude-code-user"


@dataclass
class LaunchSpec:
    """Specification for launching an agent process."""

    command: List[str]
    tool_session_id: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)


class LaunchAdapter(ABC):
    """Base class for tool-specific launch adapters."""

    tool_name: str = ""

    @abstractmethod
    def build_spawn_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build the command to spawn a new agent session."""
        ...

    @abstractmethod
    def build_continue_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build the command to continue an existing session."""
        ...

    @abstractmethod
    def build_fork_command(
        self,
        profile: ProfileSpec,
        parent_session_id: str,
        child_session_id: str,
        parent_tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build the command to fork a session."""
        ...


class ClaudeAdapter(LaunchAdapter):
    """Launch adapter for Claude Code CLI."""

    tool_name = "claude"

    def _base_command(
        self, profile: ProfileSpec, runtime_dir: Path
    ) -> tuple[List[str], Dict[str, str]]:
        """Build the base command and any extra env vars.

        isolation='compose' (default): layers profile settings on top of
        Claude Code's normal discovery chain via --settings.

        isolation='full': sets CLAUDE_CONFIG_DIR=runtime_dir so Claude owns
        no global config at all; settings.json already lives there.
        """
        cmd = [profile.executable]
        env: Dict[str, str] = {}

        claude_md = runtime_dir / "CLAUDE.md"
        if claude_md.is_file():
            cmd.extend(["--append-system-prompt-file", str(claude_md)])

        if profile.isolation == "full":
            # CLAUDE_CONFIG_DIR points at the runtime dir; settings.json is
            # already there. Copy the OAuth credential under the hashed key.
            env["CLAUDE_CONFIG_DIR"] = str(runtime_dir)
            copy_keychain_credential(runtime_dir)
        else:
            settings_file = runtime_dir / "settings.json"
            if settings_file.is_file():
                cmd.extend(["--settings", str(settings_file)])

        mcp_file = runtime_dir / "mcp.json"
        if mcp_file.is_file():
            cmd.extend(["--strict-mcp-config", "--mcp-config", str(mcp_file)])

        return cmd, env

    def build_spawn_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        cmd, env = self._base_command(profile, runtime_dir)
        cmd.extend(["--session-id", session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=session_id, env=env)

    def build_continue_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        cmd, env = self._base_command(profile, runtime_dir)
        resume_id = tool_session_id or session_id
        cmd.extend(["--resume", resume_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=resume_id, env=env)

    def build_fork_command(
        self,
        profile: ProfileSpec,
        parent_session_id: str,
        child_session_id: str,
        parent_tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        cmd, env = self._base_command(profile, runtime_dir)
        parent_id = parent_tool_session_id or parent_session_id
        cmd.extend(["--resume", parent_id, "--fork-session", "--session-id", child_session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=child_session_id, env=env)


class CodexAdapter(LaunchAdapter):
    """Launch adapter for OpenAI Codex CLI."""

    tool_name = "codex"

    def _base_command(self, profile: ProfileSpec, runtime_dir: Path) -> List[str]:
        cmd = [profile.executable]
        instructions = runtime_dir / "INSTRUCTIONS.md"
        if instructions.is_file():
            text = instructions.read_text().strip()
            if text:
                cmd.extend(["-c", f"instructions={text}"])
        return cmd

    def build_spawn_command(
        self,
        profile: ProfileSpec,
        session_id: str,  # noqa: ARG002
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        del session_id
        cmd = self._base_command(profile, runtime_dir)
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=None)

    def build_continue_command(
        self,
        profile: ProfileSpec,
        session_id: str,  # noqa: ARG002
        tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        del session_id
        cmd = self._base_command(profile, runtime_dir)
        if tool_session_id:
            cmd.extend(["--resume", tool_session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=tool_session_id)

    def build_fork_command(
        self,
        profile: ProfileSpec,
        parent_session_id: str,
        child_session_id: str,  # noqa: ARG002
        parent_tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        del child_session_id
        return self.build_continue_command(
            profile, parent_session_id, parent_tool_session_id, runtime_dir, extra_args
        )


_ADAPTERS: Dict[str, Type[LaunchAdapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
}


def get_adapter(tool: str) -> LaunchAdapter:
    """Get the launch adapter for a tool."""
    adapter_cls = _ADAPTERS.get(tool)
    if adapter_cls is None:
        available = ", ".join(sorted(_ADAPTERS.keys()))
        raise ValueError(f"No adapter for tool '{tool}'. Available: {available}")
    return adapter_cls()


def get_adapter_for_profile(profile: ProfileSpec) -> LaunchAdapter:
    """Get the launch adapter for a profile based on its manifest."""
    return get_adapter(profile.tool)
