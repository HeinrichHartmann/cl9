"""Launch adapters for different agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Type

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
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build the command to fork a session."""
        ...


class ClaudeAdapter(LaunchAdapter):
    """Launch adapter for Claude Code CLI."""

    tool_name = "claude"

    def _base_command(self, profile: ProfileSpec) -> List[str]:
        """Build the base Claude command with profile configuration."""
        cmd = [profile.executable, "--setting-sources", "user"]

        claude_md = profile.claude_md
        if claude_md.is_file():
            cmd.extend(["--append-system-prompt-file", str(claude_md.resolve())])

        settings_file = profile.settings_json
        if settings_file.is_file():
            cmd.extend(["--settings", str(settings_file.resolve())])

        mcp_file = profile.mcp_json
        if mcp_file.is_file():
            cmd.extend(["--mcp-config", str(mcp_file.resolve())])

        return cmd

    def build_spawn_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build spawn command for Claude Code."""
        cmd = self._base_command(profile)
        cmd.extend(["--session-id", session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=session_id)

    def build_continue_command(
        self,
        profile: ProfileSpec,
        session_id: str,
        tool_session_id: Optional[str],
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build continue command for Claude Code."""
        cmd = self._base_command(profile)
        # Claude Code uses the same session ID for cl9 and tool
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
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build fork command for Claude Code."""
        cmd = self._base_command(profile)
        parent_id = parent_tool_session_id or parent_session_id
        cmd.extend(["--resume", parent_id, "--fork-session", "--session-id", child_session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=child_session_id)


class CodexAdapter(LaunchAdapter):
    """Launch adapter for OpenAI Codex CLI."""

    tool_name = "codex"

    def _base_command(self, profile: ProfileSpec) -> List[str]:
        """Build the base Codex command with profile configuration."""
        cmd = [profile.executable]

        # Codex uses --instructions for system prompt
        instructions = profile.instructions_md
        if instructions.is_file():
            cmd.extend(["--instructions", str(instructions.resolve())])

        return cmd

    def build_spawn_command(
        self,
        profile: ProfileSpec,
        session_id: str,  # noqa: ARG002 - Codex doesn't support session ID injection
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build spawn command for Codex.

        Note: Codex CLI does not support injected session IDs at spawn time.
        The tool_session_id will need to be discovered after the process exits.
        """
        del session_id  # Codex doesn't support session ID injection
        cmd = self._base_command(profile)
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=None)

    def build_continue_command(
        self,
        profile: ProfileSpec,
        session_id: str,  # noqa: ARG002 - uses tool_session_id instead
        tool_session_id: Optional[str],
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build continue command for Codex.

        Note: Codex session continuation requires the tool's native session ID.
        """
        del session_id  # Codex uses tool_session_id for resume
        cmd = self._base_command(profile)
        if tool_session_id:
            cmd.extend(["--resume", tool_session_id])
        cmd.extend(extra_args)
        return LaunchSpec(command=cmd, tool_session_id=tool_session_id)

    def build_fork_command(
        self,
        profile: ProfileSpec,
        parent_session_id: str,
        child_session_id: str,  # noqa: ARG002 - Codex doesn't support fork
        parent_tool_session_id: Optional[str],
        extra_args: List[str],
    ) -> LaunchSpec:
        """Build fork command for Codex.

        Note: Codex may not support fork semantics directly.
        This falls back to continue behavior.
        """
        del child_session_id  # Codex doesn't support fork - falls back to continue
        return self.build_continue_command(
            profile, parent_session_id, parent_tool_session_id, extra_args
        )


# Registry of available adapters
_ADAPTERS: Dict[str, Type[LaunchAdapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
}


def get_adapter(tool: str) -> LaunchAdapter:
    """Get the launch adapter for a tool.

    Args:
        tool: Tool identifier (e.g., 'claude', 'codex')

    Returns:
        Instantiated adapter for the tool

    Raises:
        ValueError: If no adapter exists for the tool
    """
    adapter_cls = _ADAPTERS.get(tool)
    if adapter_cls is None:
        available = ", ".join(sorted(_ADAPTERS.keys()))
        raise ValueError(f"No adapter for tool '{tool}'. Available: {available}")
    return adapter_cls()


def get_adapter_for_profile(profile: ProfileSpec) -> LaunchAdapter:
    """Get the launch adapter for a profile based on its manifest."""
    return get_adapter(profile.tool)
