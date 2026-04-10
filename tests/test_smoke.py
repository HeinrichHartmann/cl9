"""Smoke tests for cl9 spawn pipeline.

These tests launch real processes in tmux panes and verify that
the agent starts correctly with the expected profile and statusline.
Requires: tmux, claude (or a mock), uv.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


def _tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _run(cmd: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)


def _tmux_capture(session: str) -> str:
    result = _run(f"tmux capture-pane -t {session} -p -S -200")
    return result.stdout


def _tmux_kill(session: str):
    _run(f"tmux kill-session -t {session} 2>/dev/null")


@unittest.skipUnless(_tmux_available(), "tmux not installed")
class SpawnSmokeTests(unittest.TestCase):
    """End-to-end smoke tests that spawn real cl9 sessions in tmux."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cl9-smoke-")
        self.session = f"cl9smoke-{os.getpid()}"
        self.cl9 = shutil.which("cl9")
        if not self.cl9:
            self.skipTest("cl9 not installed")

    def tearDown(self):
        _tmux_kill(self.session)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _init_project(self, name="smoketest", env_type="default"):
        """Initialize a cl9 project in self.tmpdir."""
        result = _run(
            f"{self.cl9} init {self.tmpdir} --name {name} --type {env_type}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def _set_project_config(self, **kwargs):
        """Merge keys into the project's .cl9/config.json."""
        config_path = Path(self.tmpdir) / ".cl9" / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        config.update(kwargs)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

    def _spawn_in_tmux(self, profile=None, extra_args="", wait=8):
        """Spawn cl9 agent in a tmux pane and wait for startup."""
        profile_flag = f"-p {profile}" if profile else ""
        cmd = (
            f"cd {self.tmpdir} && "
            f"{self.cl9} agent spawn {profile_flag} {extra_args}"
        )
        _run(f"tmux new-session -d -s {self.session} -x 160 -y 40 '{cmd}'")
        time.sleep(wait)

    def _capture(self) -> str:
        return _tmux_capture(self.session)

    def test_spawn_default_profile(self):
        """cl9 spawn with default profile should show the launch banner."""
        self._init_project()
        self._spawn_in_tmux()
        output = self._capture()
        self.assertIn("Profile: default", output)
        self.assertIn("Launching agent in project: smoketest", output)

    def test_spawn_default_profile_statusline(self):
        """Default profile's statusline should render via uv shebang."""
        self._init_project()
        self._spawn_in_tmux(extra_args="-- --debug-file /tmp/cl9-smoke-debug.log")
        output = self._capture()
        self.assertIn("Profile: default", output)

        # Check debug log for statusline result
        debug_log = Path("/tmp/cl9-smoke-debug.log")
        if debug_log.exists():
            log_content = debug_log.read_text()
            # Statusline should either succeed (no WARN) or at least be invoked
            if "StatusLine" in log_content:
                self.assertNotIn(
                    "completed with status 1",
                    log_content,
                    "Statusline script failed — check uv shebang and script",
                )

    def test_spawn_with_named_profile(self):
        """cl9 spawn -p <name> should use the specified profile."""
        self._init_project()
        # Use the builtin codex profile (doesn't need network)
        self._spawn_in_tmux(profile="codex")
        output = self._capture()
        self.assertIn("Profile: codex", output)

    def test_statusline_script_runs_standalone(self):
        """The default profile's statusline.py should execute via uv shebang."""
        from cl9.profiles import builtin_profile

        profile = builtin_profile("default")
        self.assertIsNotNone(profile)
        statusline = profile.path / "statusline.py"
        self.assertTrue(statusline.exists())

        # Verify executable bit
        self.assertTrue(
            os.access(statusline, os.X_OK),
            f"{statusline} is not executable",
        )

        # Run with minimal JSON input
        result = subprocess.run(
            [str(statusline)],
            input='{"model":{"display_name":"test"},"context_window":{},"cost":{}}',
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "CL9_PROJECT_ROOT": self.tmpdir,
                "CL9_PROFILE_NAME": "default",
            },
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("cl9", result.stdout.lower().replace("\033", ""))


if __name__ == "__main__":
    unittest.main()
