"""Tests for cl9.agent module and runtime-directory helpers."""

import json
import stat
import tempfile
import unittest
from pathlib import Path

import cl9.agent as agent
from cl9.profiles import ProfileSpec
from cl9.runtime import (
    materialize_profile_into_runtime,
    remove_runtime,
    runtime_dir_for,
    write_agent_config,
)
from cl9.sessions import ProjectState


class AgentResetTests(unittest.TestCase):
    """cl9.agent._reset isolates state between invocations."""

    def setUp(self):
        agent._reset(
            project_root=Path("/tmp/default"),
            profile_name="default",
            profile_dir=Path("/tmp/profile"),
            runtime_dir=Path("/tmp/runtime"),
            session_id="default-session",
        )

    def _reset(self, **kwargs):
        defaults = dict(
            project_root=Path("/tmp/proj"),
            profile_name="default",
            profile_dir=Path("/tmp/profile"),
            runtime_dir=Path("/tmp/runtime"),
            session_id="test-session-id",
            session_name=None,
            settings_baseline={},
            mcp_baseline={},
        )
        defaults.update(kwargs)
        agent._reset(**defaults)

    def test_reset_clears_mutable_dicts(self):
        agent.env["KEY"] = "val"
        agent.settings["x"] = 1
        agent.mcp["y"] = 2

        self._reset()

        self.assertEqual(agent.env, {})
        self.assertEqual(agent.settings, {})
        self.assertEqual(agent.mcp, {})

    def test_reset_sets_context_attributes(self):
        root = Path("/my/project")
        prof_dir = Path("/profile/dir")
        rt_dir = Path("/runtime/dir")

        self._reset(
            project_root=root,
            profile_name="codex",
            profile_dir=prof_dir,
            runtime_dir=rt_dir,
            session_id="abc-123",
            session_name="branch-a",
        )

        self.assertEqual(agent.project_root, root)
        self.assertEqual(agent.profile_name, "codex")
        self.assertEqual(agent.profile_dir, prof_dir)
        self.assertEqual(agent.runtime_dir, rt_dir)
        self.assertEqual(agent.session_id, "abc-123")
        self.assertEqual(agent.session_name, "branch-a")

    def test_reset_loads_baseline_dicts(self):
        self._reset(
            settings_baseline={"model": "opus"},
            mcp_baseline={"mcpServers": {"my-server": {}}},
        )

        self.assertEqual(agent.settings, {"model": "opus"})
        self.assertEqual(agent.mcp, {"mcpServers": {"my-server": {}}})

    def test_reset_does_not_share_baseline_reference(self):
        baseline = {"model": "opus"}
        self._reset(settings_baseline=baseline)
        agent.settings["extra"] = "injected"

        # The original baseline must not be mutated
        self.assertNotIn("extra", baseline)

    def test_reset_isolates_state_between_runs(self):
        self._reset(
            session_id="first",
            settings_baseline={"model": "sonnet"},
        )
        agent.env["FIRST"] = "yes"

        self._reset(session_id="second")

        self.assertEqual(agent.session_id, "second")
        self.assertEqual(agent.env, {})
        self.assertEqual(agent.settings, {})

    def test_mutations_after_reset_are_visible(self):
        self._reset()
        agent.env["ANTHROPIC_API_KEY"] = "sk-test"

        self.assertEqual(agent.env["ANTHROPIC_API_KEY"], "sk-test")


class MaterializeProfileTests(unittest.TestCase):
    """materialize_profile_into_runtime copies the right files."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.base = Path(self.tmpdir.name)
        self.profile_dir = self.base / "profile"
        self.runtime_dir = self.base / "runtime"
        self.profile_dir.mkdir()
        self.runtime_dir.mkdir()

    def _make_profile(self, files: dict[str, str]) -> ProfileSpec:
        for name, content in files.items():
            path = self.profile_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return ProfileSpec(
            name="test",
            path=self.profile_dir,
            manifest={"tool": "claude", "executable": "claude"},
        )

    def test_copies_regular_files(self):
        profile = self._make_profile({"CLAUDE.md": "# test\n", "statusline.py": "print('ok')\n"})
        materialize_profile_into_runtime(profile, self.runtime_dir)

        self.assertTrue((self.runtime_dir / "CLAUDE.md").exists())
        self.assertTrue((self.runtime_dir / "statusline.py").exists())
        self.assertEqual((self.runtime_dir / "CLAUDE.md").read_text(), "# test\n")

    def test_skips_config_files(self):
        profile = self._make_profile({
            "CLAUDE.md": "# test\n",
            "manifest.json": '{"tool":"claude"}',
            "settings.json": '{"model":"opus"}',
            "mcp.json": '{"mcpServers":{}}',
            "statusline.py": "print('ok')\n",
        })
        materialize_profile_into_runtime(profile, self.runtime_dir)

        self.assertTrue((self.runtime_dir / "CLAUDE.md").exists())
        self.assertTrue((self.runtime_dir / "statusline.py").exists())
        self.assertFalse((self.runtime_dir / "manifest.json").exists())
        self.assertFalse((self.runtime_dir / "settings.json").exists())
        self.assertFalse((self.runtime_dir / "mcp.json").exists())

    def test_preserves_mode_bits(self):
        profile = self._make_profile({"statusline.py": "print('ok')\n"})
        src = self.profile_dir / "statusline.py"
        src.chmod(src.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        materialize_profile_into_runtime(profile, self.runtime_dir)

        dest = self.runtime_dir / "statusline.py"
        self.assertTrue(dest.stat().st_mode & stat.S_IXUSR)

    def test_creates_subdirectories(self):
        profile = self._make_profile({"sub/dir/file.txt": "nested\n"})
        materialize_profile_into_runtime(profile, self.runtime_dir)

        self.assertTrue((self.runtime_dir / "sub" / "dir" / "file.txt").exists())

    def test_skip_list_applies_to_top_level_only(self):
        # A nested settings.json (e.g. an MCP server's config) must NOT be skipped.
        profile = self._make_profile({
            "settings.json": '{"top": true}',
            "sub/settings.json": '{"nested": true}',
        })
        materialize_profile_into_runtime(profile, self.runtime_dir)

        self.assertFalse((self.runtime_dir / "settings.json").exists())
        self.assertTrue((self.runtime_dir / "sub" / "settings.json").exists())


class WriteAgentConfigTests(unittest.TestCase):
    """write_agent_config serializes agent state to the runtime dir."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.runtime_dir = Path(self.tmpdir.name)
        agent._reset(
            project_root=Path("/proj"),
            profile_name="default",
            profile_dir=Path("/profile"),
            runtime_dir=self.runtime_dir,
            session_id="s1",
            session_name=None,
            settings_baseline={},
            mcp_baseline={},
        )

    def test_writes_settings_when_non_empty(self):
        agent.settings["model"] = "opus"
        write_agent_config(self.runtime_dir)

        data = json.loads((self.runtime_dir / "settings.json").read_text())
        self.assertEqual(data["model"], "opus")

    def test_writes_mcp_when_non_empty(self):
        agent.mcp["mcpServers"] = {"srv": {}}
        write_agent_config(self.runtime_dir)

        data = json.loads((self.runtime_dir / "mcp.json").read_text())
        self.assertIn("mcpServers", data)

    def test_skips_settings_when_empty(self):
        write_agent_config(self.runtime_dir)
        self.assertFalse((self.runtime_dir / "settings.json").exists())

    def test_skips_mcp_when_empty(self):
        write_agent_config(self.runtime_dir)
        self.assertFalse((self.runtime_dir / "mcp.json").exists())


class RemoveRuntimeTests(unittest.TestCase):
    """remove_runtime cleans up runtime and session dirs."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.project_root = Path(self.tmpdir.name) / "project"
        self.project_root.mkdir()

    def test_removes_runtime_dir(self):
        sid = "test-session-id"
        runtime = runtime_dir_for(self.project_root, sid)
        runtime.mkdir(parents=True)
        (runtime / "CLAUDE.md").write_text("# hi\n")

        remove_runtime(self.project_root, sid)

        self.assertFalse(runtime.exists())

    def test_removes_empty_session_dir(self):
        sid = "test-session-id"
        runtime = runtime_dir_for(self.project_root, sid)
        runtime.mkdir(parents=True)

        remove_runtime(self.project_root, sid)

        session_dir = self.project_root / ".cl9" / "sessions" / sid
        self.assertFalse(session_dir.exists())

    def test_leaves_non_empty_session_dir(self):
        sid = "test-session-id"
        runtime = runtime_dir_for(self.project_root, sid)
        runtime.mkdir(parents=True)
        session_dir = self.project_root / ".cl9" / "sessions" / sid
        (session_dir / "extra.json").write_text("{}\n")

        remove_runtime(self.project_root, sid)

        self.assertFalse(runtime.exists())
        self.assertTrue(session_dir.exists())

    def test_idempotent_when_runtime_missing(self):
        # Should not raise even if runtime dir was never created
        remove_runtime(self.project_root, "nonexistent-session")


class SessionRuntimeLifecycleTests(unittest.TestCase):
    """session delete and prune remove runtime directories."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.project_root = Path(self.tmpdir.name) / "project"
        self.project_root.mkdir()
        self.state = ProjectState(self.project_root)

    def _create_session_with_runtime(self, session_id, name=None):
        self.state.create_session(session_id, name, "default", "claude", self.project_root)
        runtime = runtime_dir_for(self.project_root, session_id)
        runtime.mkdir(parents=True)
        (runtime / "CLAUDE.md").write_text("# test\n")
        return runtime

    def test_delete_session_removes_runtime_dir(self):
        sid = "aaaa-delete"
        runtime = self._create_session_with_runtime(sid)

        self.state.delete_session(sid)

        self.assertFalse(runtime.exists())

    def test_prune_sessions_removes_runtime_dirs(self):
        import datetime
        sid = "bbbb-prune"
        runtime = self._create_session_with_runtime(sid)

        # Back-date the session so it qualifies for pruning
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=31)).isoformat()
        with self.state._connect() as conn:
            conn.execute(
                "UPDATE agent_sessions SET last_used_at = ? WHERE session_id = ?",
                (cutoff, sid),
            )
            conn.commit()

        pruned = self.state.prune_sessions(older_than_days=30)

        self.assertEqual(pruned, 1)
        self.assertFalse(runtime.exists())


if __name__ == "__main__":
    unittest.main()
