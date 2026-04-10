#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render a compact Claude Code status line for cl9 projects."""

from __future__ import annotations

import json
import os
import re
import sys


RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def color(text: str, code: str) -> str:
    """Wrap text with an ANSI color code."""
    return f"{code}{text}{RESET}"


def human_tokens(count: int) -> str:
    """Format token counts compactly."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


_BAR_WIDTH = 10
_EIGHTHS = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]


def context_bar(used_pct: int) -> str:
    """Render a smooth Unicode usage bar."""
    eighths = max(0, min(_BAR_WIDTH * 8, round(used_pct * _BAR_WIDTH * 8 / 100)))
    full, rem = divmod(eighths, 8)
    bar = "█" * full
    if full < _BAR_WIDTH:
        bar += _EIGHTHS[rem]
        bar += " " * (_BAR_WIDTH - full - 1)
    return bar


def model_color(model_name: str) -> str:
    """Choose a color for the current model."""
    lowered = model_name.lower()
    if "opus" in lowered:
        return MAGENTA
    if "sonnet" in lowered:
        return BLUE
    if "haiku" in lowered:
        return CYAN
    return BOLD


def context_color(used_pct: int) -> str:
    """Choose a color based on context saturation."""
    if used_pct >= 80:
        return RED
    if used_pct >= 60:
        return YELLOW
    return GREEN


def to_int(value: object) -> int:
    """Convert a statusline numeric field to int safely."""
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def context_percentage(context: dict) -> int:
    """Return a robust context percentage for Claude payloads."""
    used_pct = to_int(context.get("used_percentage"))
    if used_pct > 0:
        return min(100, used_pct)

    window_size = to_int(context.get("context_window_size"))
    current_usage = context.get("current_usage") or {}
    if window_size <= 0 or not isinstance(current_usage, dict):
        return max(0, min(100, used_pct))

    used_tokens = (
        to_int(current_usage.get("input_tokens"))
        + to_int(current_usage.get("cache_creation_input_tokens"))
        + to_int(current_usage.get("cache_read_input_tokens"))
    )
    if used_tokens <= 0:
        return max(0, min(100, used_pct))

    return min(100, int((used_tokens * 100) / window_size))


_MODEL_PREFIX = re.compile(r"^(bedrock/)?(anthropic\.)?")


def short_model(name: str) -> str:
    """Collapse verbose model IDs to human names.

    E.g. ``bedrock/anthropic.claude-sonnet-4-6`` becomes ``Sonnet 4.6``.
    """
    if not name:
        return "Claude"
    stripped = _MODEL_PREFIX.sub("", name)
    stripped = stripped.replace(":", "-")
    parts = stripped.split("-")
    if parts and parts[0].lower() == "claude":
        parts = parts[1:]
    if not parts:
        return name
    family = parts[0].capitalize()
    version_parts: list[str] = []
    for p in parts[1:]:
        if p.isdigit():
            version_parts.append(p)
        elif re.fullmatch(r"\d{8}", p):
            break
        else:
            break
    if version_parts:
        return f"{family} {'.'.join(version_parts)}"
    return family


def main() -> int:
    """Render the status line from Claude Code JSON input."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    project_root = os.environ.get("CL9_PROJECT_ROOT", "")
    project = (
        os.path.basename(project_root)
        if project_root
        else os.path.basename(os.getcwd())
    )
    profile = os.environ.get("CL9_PROFILE_NAME", "default")
    raw_model = data.get("model", {}).get("display_name") or data.get("model", {}).get(
        "id", ""
    )
    model = short_model(raw_model)
    session = data.get("session_name") or profile
    context = data.get("context_window", {})
    used_pct = context_percentage(context)
    window_size = to_int(context.get("context_window_size"))
    cost = float(data.get("cost", {}).get("total_cost_usd", 0) or 0)

    cl9_badge = color("cl9", DIM)
    project_text = color(project, CYAN)
    session_text = color(session, DIM)
    model_text = color(model, model_color(raw_model))

    bar = context_bar(used_pct)
    usage = f"{bar} {used_pct:>3}%"
    if window_size > 0:
        usage = f"{usage} / {human_tokens(window_size)}"
    usage_text = color(usage, context_color(used_pct))

    parts = [f"{cl9_badge} {project_text}", session_text, model_text, usage_text]
    if cost > 0:
        parts.append(color(f"${cost:.2f}", DIM))

    print(" │ ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
