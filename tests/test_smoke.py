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


def _claude_available() -> bool:
    return shutil.which("claude") is not None


@unittest.skipUnless(_claude_available(), "claude not installed")
class ClaudeLiveSmokeTests(unittest.TestCase):
    """Smoke tests that call claude directly via cl9 spawn -- -p.

    These hit the real API. If auth fails, log in once via:
        cl9 agent spawn   (in any cl9 project)
    then re-run the tests.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cl9-live-")
        self.cl9 = shutil.which("cl9")
        if not self.cl9:
            self.skipTest("cl9 not installed")
        # Init a minimal project
        result = _run(f"{self.cl9} init {self.tmpdir} --name livetest --type minimal")
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _spawn_print(self, prompt: str, extra_flags: str = "") -> subprocess.CompletedProcess:
        """Spawn cl9 agent with -p (print mode) and return the result."""
        cmd = (
            f"cd {self.tmpdir} && "
            f"{self.cl9} agent spawn {extra_flags} "
            f"-- -p --output-format json '{prompt}'"
        )
        return _run(cmd, timeout=60)

    def test_auth_works_without_bare(self):
        """Agent should authenticate via keychain (no --bare, no API key needed).

        If this fails with an auth error, log in once:
            cd /tmp && cl9 init . --type minimal && cl9 agent spawn
        """
        result = self._spawn_print("Reply with exactly: PONG")
        if result.returncode != 0 and "auth" in result.stderr.lower():
            self.fail(
                "Auth failed. Please log in once by running:\n"
                "    cl9 agent spawn\n"
                "in any cl9 project, then re-run this test."
            )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("PONG", result.stdout)

    def test_claude_responds_correctly(self):
        """Claude should return a correct, verifiable answer."""
        result = self._spawn_print(
            "What is 7 * 6? Reply with ONLY the number, nothing else."
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Parse JSON output
        try:
            data = json.loads(result.stdout)
            response_text = data.get("result", "")
        except json.JSONDecodeError:
            response_text = result.stdout
        self.assertIn("42", response_text)

    def test_statusline_in_settings(self):
        """Default profile should inject a statusline command into settings."""
        # Spawn with default profile (not minimal) to get statusline
        shutil.rmtree(self.tmpdir)
        self.tmpdir = tempfile.mkdtemp(prefix="cl9-live-sl-")
        _run(f"{self.cl9} init {self.tmpdir} --name sltest --type minimal")

        # Use default profile which has statusline in settings.json
        result = self._spawn_print(
            "Reply with exactly: OK",
            extra_flags="-p default",
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Verify the runtime dir was created with settings.json containing statusline
        cl9_dir = Path(self.tmpdir) / ".cl9" / "sessions"
        if cl9_dir.exists():
            session_dirs = list(cl9_dir.iterdir())
            self.assertTrue(len(session_dirs) > 0, "No session directories created")
            runtime_dir = session_dirs[0] / "runtime"
            settings_file = runtime_dir / "settings.json"
            if settings_file.exists():
                settings = json.loads(settings_file.read_text())
                self.assertIn("statusLine", settings)
                self.assertEqual(settings["statusLine"]["type"], "command")
                # Verify the statusline command points to an existing file
                cmd_path = settings["statusLine"]["command"]
                # The command references ${CL9_RUNTIME_DIR} which claude expands
                resolved = cmd_path.replace("${CL9_RUNTIME_DIR}", str(runtime_dir))
                self.assertTrue(
                    Path(resolved).exists(),
                    f"Statusline script not found at {resolved}",
                )


if __name__ == "__main__":
    unittest.main()
