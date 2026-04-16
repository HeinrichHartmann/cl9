import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

import cl9.cli as cli_module
import cl9.config as config_module


class DummyPluginLoader:
    def run_hook(self, hook_name, *args):
        return False


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.base = Path(self.tmpdir.name)
        self.work_dir = self.base / "work"
        self.work_dir.mkdir()

        self._patches = [
            patch.dict(os.environ, {"HOME": str(self.base)}),
            patch.object(config_module, "user_config_dir", lambda appname: str(self.base / "xdg-config")),
            patch.object(config_module, "user_data_dir", lambda appname: str(self.base / "xdg-data")),
            patch.object(config_module, "user_cache_dir", lambda appname: str(self.base / "xdg-cache")),
        ]

        for active_patch in self._patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        self.test_config = config_module.Config()
        self._config_patches = [
            patch.object(config_module, "config", self.test_config),
            patch.object(cli_module, "config", self.test_config),
            patch.object(cli_module, "get_plugin_loader", return_value=DummyPluginLoader()),
        ]

        for active_patch in self._config_patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        self.runner = CliRunner()

    def _chdir(self, path):
        original = Path.cwd()
        os.chdir(path)
        self.addCleanup(os.chdir, original)

    def _write_local_project_config(self, project_path, name):
        cl9_dir = project_path / ".cl9"
        cl9_dir.mkdir(parents=True)
        with open(cl9_dir / "config.json", "w") as f:
            json.dump({"name": name, "version": "1"}, f)

    def _read_state(self, project_path):
        with open(project_path / ".cl9" / "env" / "state.json", "r") as f:
            return json.load(f)

    def _invoke_enter(self, args):
        captured = {}

        def fake_chdir(path):
            captured["cwd"] = str(path)

        def fake_execvpe(shell, argv, env):
            captured["shell"] = shell
            captured["argv"] = argv
            captured["env"] = dict(env)
            raise RuntimeError("exec intercepted")

        with patch.object(cli_module.os, "chdir", side_effect=fake_chdir), patch.object(
            cli_module.os,
            "execvpe",
            side_effect=fake_execvpe,
        ):
            result = self.runner.invoke(cli_module.main, args)

        self.assertEqual(result.exit_code, 1)
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertEqual(str(result.exception), "exec intercepted")
        return result, captured

    def _invoke_agent(self, args):
        captured = {}
        exit_code = 0

        class FakeProcess:
            pid = 4321

            def wait(self_nonlocal):
                return exit_code

        def fake_popen(argv, env=None):
            captured["argv"] = list(argv)
            captured["env"] = dict(env)
            captured["cwd"] = str(Path.cwd())
            return FakeProcess()

        with patch.object(cli_module.subprocess, "Popen", side_effect=fake_popen):
            result = self.runner.invoke(cli_module.main, args)

        return result, captured

    def _invoke_run(self, args):
        captured = {}

        class FakeProcess:
            pid = 8765

            def wait(self_nonlocal):
                return 0

        def fake_popen(argv, env=None):
            captured["argv"] = list(argv)
            captured["env"] = dict(env)
            captured["cwd"] = str(Path.cwd())
            return FakeProcess()

        with patch.object(cli_module.subprocess, "Popen", side_effect=fake_popen):
            result = self.runner.invoke(cli_module.main, args)

        self.assertEqual(result.exit_code, 0)
        return result, captured

    def _create_template(self, files):
        template_dir = self.work_dir / f"template-{len(list(self.work_dir.iterdir()))}"
        template_dir.mkdir()
        for rel_path, content in files.items():
            dest = template_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        return template_dir

    def test_init_default_creates_environment_and_state(self):
        project_dir = self.work_dir / "demo"
        project_dir.mkdir()

        result = self.runner.invoke(cli_module.main, ["init", str(project_dir)])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue((project_dir / "src").is_dir())
        self.assertTrue((project_dir / "doc").is_dir())
        self.assertTrue((project_dir / "data").is_dir())
        self.assertIn("# demo", (project_dir / "README.md").read_text())
        self.assertIn("demo - Agent Memory", (project_dir / "MEMORY.md").read_text())
        self.assertIn('description = "demo - cl9 project environment"', (project_dir / "flake.nix").read_text())
        self.assertFalse((project_dir / ".cl9" / "profiles").exists())
        state = self._read_state(project_dir)
        self.assertEqual(state["type"], "default")
        self.assertEqual(
            set(state["files"]),
            {
                "README.md",
                "MEMORY.md",
                "flake.nix",
                ".envrc",
            },
        )
        self.assertIsNone(self.test_config.get_project("demo"))

    def test_init_minimal_creates_only_cl9_state(self):
        project_dir = self.work_dir / "minimal"
        project_dir.mkdir()

        result = self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue((project_dir / ".cl9" / "config.json").exists())
        self.assertTrue((project_dir / ".cl9" / "env" / "state.json").exists())
        self.assertFalse((project_dir / ".cl9" / "profiles").exists())
        self.assertFalse((project_dir / "README.md").exists())
        self.assertFalse((project_dir / "src").exists())
        self.assertEqual(set(self._read_state(project_dir)["files"]), set())

    def test_init_creates_init_example_py(self):
        project_dir = self.work_dir / "init-example"
        project_dir.mkdir()

        result = self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])

        self.assertEqual(result.exit_code, 0)
        example = project_dir / ".cl9" / "init" / "init-example.py"
        self.assertTrue(example.exists())
        content = example.read_text()
        self.assertIn("from cl9 import agent", content)
        self.assertFalse((project_dir / ".cl9" / "init" / "init.py").exists())

    def test_init_force_rewrites_init_example_but_not_init_py(self):
        project_dir = self.work_dir / "init-force"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])

        # Place a custom init.py and corrupt init-example.py
        init_dir = project_dir / ".cl9" / "init"
        (init_dir / "init.py").write_text("# my custom init\n")
        (init_dir / "init-example.py").write_text("# stale\n")

        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--force"])

        # init-example.py should be restored; init.py must be untouched
        self.assertIn("from cl9 import agent", (init_dir / "init-example.py").read_text())
        self.assertEqual((init_dir / "init.py").read_text(), "# my custom init\n")

    def test_init_fails_before_writing_when_template_paths_conflict(self):
        project_dir = self.work_dir / "conflict"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("existing\n")

        result = self.runner.invoke(cli_module.main, ["init", str(project_dir)])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Move them out of the way", result.output)
        self.assertFalse((project_dir / ".cl9").exists())

    def test_init_previews_for_existing_project(self):
        project_dir = self.work_dir / "existing"
        project_dir.mkdir()

        first = self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        second = self.runner.invoke(cli_module.main, ["init", str(project_dir)])

        self.assertEqual(first.exit_code, 0)
        self.assertEqual(second.exit_code, 0)
        self.assertIn("Run 'cl9 init --force' to apply these changes.", second.output)
        self.assertEqual(self._read_state(project_dir)["type"], "minimal")

    def test_completion_command_outputs_completion_script(self):
        result = self.runner.invoke(cli_module.main, ["completion", "zsh"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("_CL9_COMPLETE=zsh_source", result.output)

    def test_man_command_outputs_generated_manual(self):
        result = self.runner.invoke(cli_module.main, ["man"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("CL9(1)", result.output)
        self.assertIn("cl9 init [PATH]", result.output)
        self.assertIn("cl9 enter TARGET", result.output)
        self.assertIn("cl9 agent spawn", result.output)
        self.assertIn("cl9 agent continue [TARGET]", result.output)
        self.assertIn("cl9 session list", result.output)
        self.assertIn("cl9 run [COMMAND_ARGV...]", result.output)
        self.assertIn("cl9 project run [COMMAND_ARGV...]", result.output)
        self.assertIn("cl9 project register [PATH]", result.output)
        self.assertIn("cl9 completion SHELL", result.output)
        self.assertIn("FILES", result.output)
        self.assertIn("SEE ALSO", result.output)

    def test_agent_spawn_finds_project_root_from_subdirectory(self):
        project_dir = self.work_dir / "agent-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        nested_dir = project_dir / "src" / "deep" / "nested"
        nested_dir.mkdir(parents=True)
        self._chdir(nested_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("12345678-1234-5678-1234-567812345678")):
            result, captured = self._invoke_agent(["agent", "spawn"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launching agent in project: agent-project", result.output)
        self.assertEqual(captured["cwd"], str(nested_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_PROJECT_ROOT"], str(project_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_PROFILE_NAME"], "default")
        self.assertEqual(captured["env"]["CL9_SESSION_ID"], "12345678-1234-5678-1234-567812345678")
        self.assertEqual(captured["env"]["CL9_SESSION_NAME"], "")
        self.assertNotIn("CL9_PROJECT", captured["env"])
        self.assertNotIn("CL9_ACTIVE", captured["env"])
        self.assertTrue(captured["env"]["PATH"].startswith(str((project_dir / "bin").resolve())))
        self.assertEqual(captured["argv"][1], "-ic")
        self.assertIn("claude", captured["argv"][2])
        self.assertNotIn("--bare", captured["argv"][2])
        self.assertIn("--append-system-prompt-file", captured["argv"][2])
        self.assertIn("CLAUDE.md", captured["argv"][2])
        self.assertIn("--settings", captured["argv"][2])
        self.assertIn("settings.json", captured["argv"][2])
        self.assertIn("--session-id 12345678-1234-5678-1234-567812345678", captured["argv"][2])

    def test_agent_spawn_full_isolation_sets_claude_config_dir(self):
        """isolation='full' profile sets CLAUDE_CONFIG_DIR instead of --settings."""
        project_dir = self.work_dir / "agent-full"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("12345678-1234-5678-1234-567812345678")), \
             patch("cl9.adapters.copy_keychain_credential"):
            result, captured = self._invoke_agent(["agent", "spawn", "-p", "default-full"])

        self.assertEqual(result.exit_code, 0)
        # CLAUDE_CONFIG_DIR should be set to the runtime dir
        self.assertIn("CLAUDE_CONFIG_DIR", captured["env"])
        runtime_dir = captured["env"]["CLAUDE_CONFIG_DIR"]
        self.assertIn("12345678-1234-5678-1234-567812345678", runtime_dir)
        # --settings flag must NOT be present (handled by CLAUDE_CONFIG_DIR)
        self.assertNotIn("--settings", captured["argv"][2])
        # --append-system-prompt-file still present for CLAUDE.md
        self.assertIn("--append-system-prompt-file", captured["argv"][2])

    def test_agent_without_subcommand_shows_help(self):
        project_dir = self.work_dir / "agent-default"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        result = self.runner.invoke(cli_module.main, ["agent"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Agent management commands.", result.output)
        self.assertIn("spawn", result.output)

    def _make_transcript(self, session_cwd: Path, session_id: str) -> None:
        """Create a fake claude transcript file so the resume guardrail passes."""
        encoded = str(session_cwd.resolve()).replace("/", "-")
        transcript = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("{}\n")

    def test_agent_continue_uses_existing_session(self):
        project_dir = self.work_dir / "agent-continue"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")):
            self._invoke_agent(["agent", "spawn", "--name", "main"])

        self._make_transcript(project_dir, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        result, captured = self._invoke_agent(["agent", "continue", "main"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--resume aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", captured["argv"][2])

    def test_agent_spawn_forwards_passthrough_args_after_double_dash(self):
        project_dir = self.work_dir / "agent-passthrough"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")):
            result, captured = self._invoke_agent(["agent", "spawn", "--", "--model", "sonnet"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--session-id eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", captured["argv"][2])
        self.assertIn("--model sonnet", captured["argv"][2])

    def test_agent_continue_forwards_passthrough_args_after_double_dash(self):
        project_dir = self.work_dir / "agent-continue-passthrough"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")):
            self._invoke_agent(["agent", "spawn", "--name", "main"])

        self._make_transcript(project_dir, "ffffffff-ffff-ffff-ffff-ffffffffffff")
        result, captured = self._invoke_agent(["agent", "continue", "main", "--", "--model", "opus"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--resume ffffffff-ffff-ffff-ffff-ffffffffffff", captured["argv"][2])
        self.assertIn("--model opus", captured["argv"][2])

    def test_agent_continue_fails_without_transcript(self):
        project_dir = self.work_dir / "agent-continue-notranscript"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")):
            self._invoke_agent(["agent", "spawn", "--name", "main"])

        # No transcript created — guardrail must fire
        result = self.runner.invoke(cli_module.main, ["agent", "continue", "main"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("transcript not found", result.output)

    def test_agent_spawn_uses_builtin_codex_profile(self):
        project_dir = self.work_dir / "agent-codex-profile"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("abababab-abab-abab-abab-abababababab")):
            result, captured = self._invoke_agent(["agent", "spawn", "--profile", "codex"])

        self.assertEqual(result.exit_code, 0)
        self.assertFalse((project_dir / ".cl9" / "profiles").exists())
        self.assertEqual(captured["env"]["CL9_PROFILE_NAME"], "codex")
        self.assertIn("-c 'instructions=", captured["argv"][2])

    def test_agent_spawn_errors_outside_project(self):
        outside_dir = self.work_dir / "outside"
        outside_dir.mkdir()
        self._chdir(outside_dir)

        result = self.runner.invoke(cli_module.main, ["agent", "spawn"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Not in a cl9 project directory", result.output)

    def test_gc_nudge_appears_when_stale_sessions_exist(self):
        """GC nudge is printed to stderr when sessions are overdue for pruning."""
        from datetime import datetime, timedelta

        project_dir = self.work_dir / "gc-nudge"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        # Spawn a session then manually backdate last_used_at so it looks stale
        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")):
            self._invoke_agent(["agent", "spawn"])

        state_db = project_dir / ".cl9" / "state.db"
        import sqlite3 as _sqlite3
        stale_time = (datetime.now() - timedelta(days=10)).isoformat()
        conn = _sqlite3.connect(str(state_db))
        conn.execute("UPDATE agent_sessions SET status='idle', last_used_at=? WHERE session_id=?",
                     (stale_time, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
        conn.commit()
        conn.close()

        result = self.runner.invoke(cli_module.main, ["session", "list"], catch_exceptions=False)

        # Nudge goes to stderr; CliRunner mixes stderr into output by default
        self.assertIn("gc has not run in a while", result.output)
        self.assertIn("1 stale session", result.output)

    def test_session_list_shows_project_local_sessions(self):
        project_dir = self.work_dir / "session-list"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        self._chdir(project_dir)

        with patch.object(cli_module.uuid, "uuid4", return_value=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")):
            self._invoke_agent(["agent", "spawn", "--name", "architect"])

        result = self.runner.invoke(cli_module.main, ["session", "list"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("architect", result.output)
        self.assertIn("cccccccc-cccc-cccc-cccc-cccccccccccc", result.output)

    def test_project_run_uses_project_environment(self):
        project_dir = self.work_dir / "run-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])
        nested_dir = project_dir / "src"
        nested_dir.mkdir()
        self._chdir(nested_dir)

        _, captured = self._invoke_run(["run", "snapshot", "--fast"])

        self.assertEqual(captured["env"]["CL9_PROJECT"], "run-project")
        self.assertEqual(captured["env"]["CL9_PROJECT_PATH"], str(project_dir.resolve()))
        self.assertTrue(captured["env"]["PATH"].startswith(str((project_dir / "bin").resolve())))
        self.assertIn("cd ", captured["argv"][2])
        self.assertIn("exec snapshot --fast", captured["argv"][2])

    def test_default_statusline_renders_project_model_and_context(self):
        script_path = Path(cli_module.__file__).parent / "profiles" / "default" / "statusline.py"
        payload = json.dumps(
            {
                "model": {"display_name": "Opus"},
                "session_name": "branch-a",
                "context_window": {"used_percentage": 58, "context_window_size": 200000},
                "cost": {"total_cost_usd": 1.23},
            }
        )
        env = os.environ.copy()
        env["CL9_PROJECT_ROOT"] = "/work/demo"
        env["CL9_PROFILE_NAME"] = "careful"

        result = subprocess.run(
            [sys.executable, str(script_path)],
            input=payload,
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )

        self.assertIn("demo", result.stdout)
        self.assertIn("branch-a", result.stdout)
        self.assertIn("Opus", result.stdout)
        self.assertIn("58%", result.stdout)
        self.assertIn("200k", result.stdout)

    def test_default_statusline_falls_back_to_current_usage_for_context(self):
        script_path = Path(cli_module.__file__).parent / "profiles" / "default" / "statusline.py"
        payload = json.dumps(
            {
                "model": {"display_name": "Sonnet"},
                "context_window": {
                    "used_percentage": 0,
                    "context_window_size": 200000,
                    "current_usage": {
                        "input_tokens": 60000,
                        "cache_creation_input_tokens": 20000,
                        "cache_read_input_tokens": 20000,
                        "output_tokens": 5000,
                    },
                },
            }
        )
        env = os.environ.copy()
        env["CL9_PROJECT_ROOT"] = "/work/demo"

        result = subprocess.run(
            [sys.executable, str(script_path)],
            input=payload,
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )

        self.assertIn("50%", result.stdout)

    def test_project_register_adds_initialized_project_to_registry(self):
        project_dir = self.work_dir / "register-me"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])

        result = self.runner.invoke(cli_module.main, ["project", "register", str(project_dir)])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.test_config.get_project("register-me")["path"], str(project_dir.resolve()))

    def test_project_register_updates_stale_registration_for_moved_project(self):
        old_dir = self.work_dir / "old-location"
        old_dir.mkdir()
        self._write_local_project_config(old_dir, "moved-project")
        self.test_config.add_project("moved-project", old_dir)
        self.assertTrue(self.test_config.get_project("moved-project"))
        old_dir.rename(self.work_dir / "new-location")
        new_dir = self.work_dir / "new-location"

        result = self.runner.invoke(cli_module.main, ["project", "register", str(new_dir)])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.test_config.get_project("moved-project")["path"], str(new_dir.resolve()))

    def test_project_prune_removes_missing_projects(self):
        missing_dir = self.work_dir / "missing-project"
        self.test_config.add_project("missing-project", missing_dir)

        result = self.runner.invoke(cli_module.main, ["project", "prune"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Pruned project 'missing-project'", result.output)
        self.assertFalse(self.test_config.project_exists("missing-project"))

    def test_enter_path_mode_uses_local_config_for_unregistered_project(self):
        project_dir = self.work_dir / "local-only"
        project_dir.mkdir()
        self._write_local_project_config(project_dir, "local-config-name")

        _, captured = self._invoke_enter(["enter", str(project_dir)])

        self.assertEqual(captured["cwd"], str(project_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_PROJECT"], "local-config-name")
        self.assertEqual(captured["env"]["CL9_PROJECT_PATH"], str(project_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_ACTIVE"], "1")
        self.assertTrue(captured["env"]["PATH"].startswith(str((project_dir / "bin").resolve())))

    def test_enter_smart_mode_prefers_registry_name_over_matching_path(self):
        registered_dir = self.work_dir / "registered-target"
        registered_dir.mkdir()
        self._write_local_project_config(registered_dir, "foo")
        self.test_config.add_project("foo", registered_dir)

        local_path = self.work_dir / "foo"
        local_path.mkdir()
        self._write_local_project_config(local_path, "local-foo")
        self._chdir(self.work_dir)

        _, captured = self._invoke_enter(["enter", "foo"])

        self.assertEqual(captured["cwd"], str(registered_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_PROJECT"], "foo")
        self.assertEqual(captured["env"]["CL9_PROJECT_PATH"], str(registered_dir.resolve()))

    def test_enter_path_flag_forces_path_resolution(self):
        registered_dir = self.work_dir / "registered-target-2"
        registered_dir.mkdir()
        self._write_local_project_config(registered_dir, "foo")
        self.test_config.add_project("foo", registered_dir)

        local_path = self.work_dir / "foo"
        local_path.mkdir()
        self._write_local_project_config(local_path, "local-foo")
        self._chdir(self.work_dir)

        _, captured = self._invoke_enter(["enter", "--path", "foo"])

        self.assertEqual(captured["cwd"], str(local_path.resolve()))
        self.assertEqual(captured["env"]["CL9_PROJECT"], "local-foo")
        self.assertEqual(captured["env"]["CL9_PROJECT_PATH"], str(local_path.resolve()))

    def test_init_existing_project_previews_template_changes(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "preview-project"
        project_dir.mkdir()

        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", str(template_dir)])

        (template_dir / "README.md").write_text("# two\n")
        result = self.runner.invoke(cli_module.main, ["init", str(project_dir)])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Would clobber:   README.md", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# one\n")

    def test_init_force_overwrites_template_files(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "force-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", str(template_dir)])

        (template_dir / "README.md").write_text("# two\n")
        result = self.runner.invoke(cli_module.main, ["init", str(project_dir), "--force"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Overwrote:   README.md", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# two\n")


if __name__ == "__main__":
    unittest.main()
