"""Launch adapters for different agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Type

from .profiles import ProfileSpec


@dataclass
class LaunchSpec:
    """Specification for launching an agent process."""

    command: List[str]
    tool_session_id: Optional[str] = None


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

    def _base_command(self, profile: ProfileSpec, runtime_dir: Path) -> List[str]:
        """Build the base command pointing at the session runtime dir.

        Note: we intentionally do NOT use ``--bare``. Bare mode strips
        MCP servers, hooks, skills, statusline, and most tools — it is
        designed for scripted one-shot ``claude -p`` calls, not
        interactive agent sessions. Our settings and MCP config are
        injected via ``--settings`` / ``--mcp-config`` which merge on
        top of Claude Code's normal discovery chain.
        """
        cmd = [profile.executable]

        claude_md = runtime_dir / "CLAUDE.md"
        if claude_md.is_file():
            cmd.extend(["--append-system-prompt-file", str(claude_md)])

        settings_file = runtime_dir / "settings.json"
        if settings_file.is_file():
            cmd.extend(["--settings", str(settings_file)])

        mcp_file = runtime_dir / "mcp.json"
        if mcp_file.is_file():
            cmd.extend(["--mcp-config", str(mcp_file)])

        return cmd

    def build_spawn_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        cmd = self._base_command(profile, runtime_dir)
        cmd.extend(["--session-id", session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=session_id)

    def build_continue_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        cmd = self._base_command(profile, runtime_dir)
        resume_id = tool_session_id or session_id
        cmd.extend(["--resume", resume_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=resume_id)

    def build_fork_command(
        self,
        profile: ProfileSpec,
        parent_session_id: str,
        child_session_id: str,
        parent_tool_session_id: Optional[str],
        runtime_dir: Path,
        extra_args: List[str],
    ) -> LaunchSpec:
        cmd = self._base_command(profile, runtime_dir)
        parent_id = parent_tool_session_id or parent_session_id
        cmd.extend(["--resume", parent_id, "--fork-session", "--session-id", child_session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=child_session_id)


class CodexAdapter(LaunchAdapter):
    """Launch adapter for OpenAI Codex CLI."""

    tool_name = "codex"

    def _base_command(self, profile: ProfileSpec, runtime_dir: Path) -> List[str]:
        cmd = [profile.executable]
        instructions = runtime_dir / "INSTRUCTIONS.md"
        if instructions.is_file():
            cmd.extend(["--instructions", str(instructions)])
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
