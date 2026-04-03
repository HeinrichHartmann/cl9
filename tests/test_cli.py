import json
import os
import tempfile
import unittest
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
        state = self._read_state(project_dir)
        self.assertEqual(state["type"], "default")
        self.assertEqual(
            set(state["files"]),
            {"README.md", "MEMORY.md", "flake.nix", ".envrc"},
        )

    def test_init_minimal_creates_only_cl9_state(self):
        project_dir = self.work_dir / "minimal"
        project_dir.mkdir()

        result = self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", "minimal"])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue((project_dir / ".cl9" / "config.json").exists())
        self.assertTrue((project_dir / ".cl9" / "env" / "state.json").exists())
        self.assertFalse((project_dir / "README.md").exists())
        self.assertFalse((project_dir / "src").exists())
        self.assertEqual(self._read_state(project_dir)["files"], {})

    def test_init_fails_before_writing_when_template_paths_conflict(self):
        project_dir = self.work_dir / "conflict"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("existing\n")

        result = self.runner.invoke(cli_module.main, ["init", str(project_dir)])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Move them out of the way", result.output)
        self.assertFalse((project_dir / ".cl9").exists())

    def test_env_init_alias_invokes_init(self):
        project_dir = self.work_dir / "alias"
        project_dir.mkdir()

        result = self.runner.invoke(cli_module.main, ["env", "init", str(project_dir), "--type", "minimal"])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue((project_dir / ".cl9" / "config.json").exists())
        self.assertEqual(self._read_state(project_dir)["type"], "minimal")

    def test_env_shell_subcommand_outputs_completion_script(self):
        result = self.runner.invoke(cli_module.main, ["env", "zsh"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("_CL9_COMPLETE=zsh_source", result.output)

    def test_enter_path_mode_uses_local_config_for_unregistered_project(self):
        project_dir = self.work_dir / "local-only"
        project_dir.mkdir()
        self._write_local_project_config(project_dir, "local-config-name")

        _, captured = self._invoke_enter(["enter", str(project_dir)])

        self.assertEqual(captured["cwd"], str(project_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_PROJECT"], "local-config-name")
        self.assertEqual(captured["env"]["CL9_PROJECT_PATH"], str(project_dir.resolve()))
        self.assertEqual(captured["env"]["CL9_ACTIVE"], "1")

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

    def test_env_update_updates_unchanged_files(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "update-project"
        project_dir.mkdir()

        init_result = self.runner.invoke(
            cli_module.main,
            ["init", str(project_dir), "--type", str(template_dir)],
        )
        self.assertEqual(init_result.exit_code, 0)

        (template_dir / "README.md").write_text("# two\n")
        self._chdir(project_dir)
        result = self.runner.invoke(cli_module.main, ["env", "update"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Updated:     README.md", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# two\n")
        state = self._read_state(project_dir)
        self.assertTrue(state["files"]["README.md"].startswith("sha256:"))

    def test_env_update_skips_modified_files(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "skip-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", str(template_dir)])

        (project_dir / "README.md").write_text("# user change\n")
        (template_dir / "README.md").write_text("# two\n")
        self._chdir(project_dir)
        result = self.runner.invoke(cli_module.main, ["env", "update"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Skipped:       README.md (modified by user)", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# user change\n")

    def test_env_update_force_overwrites_modified_files(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "force-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", str(template_dir)])

        (project_dir / "README.md").write_text("# user change\n")
        (template_dir / "README.md").write_text("# two\n")
        self._chdir(project_dir)
        result = self.runner.invoke(cli_module.main, ["env", "update", "--force"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Updated:     README.md (was modified)", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# two\n")

    def test_env_update_diff_does_not_modify_files(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "diff-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", str(template_dir)])

        (template_dir / "README.md").write_text("# two\n")
        self._chdir(project_dir)
        result = self.runner.invoke(cli_module.main, ["env", "update", "--diff"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Would update:     README.md", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# one\n")

    def test_env_update_readds_deleted_file(self):
        template_dir = self._create_template({"README.md": "# one\n"})
        project_dir = self.work_dir / "readd-project"
        project_dir.mkdir()
        self.runner.invoke(cli_module.main, ["init", str(project_dir), "--type", str(template_dir)])

        (project_dir / "README.md").unlink()
        self._chdir(project_dir)
        result = self.runner.invoke(cli_module.main, ["env", "update"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Added:        README.md", result.output)
        self.assertEqual((project_dir / "README.md").read_text(), "# one\n")

    def test_env_update_requires_state_tracking(self):
        project_dir = self.work_dir / "nostate-project"
        project_dir.mkdir()
        self._write_local_project_config(project_dir, "nostate-project")
        self._chdir(project_dir)

        result = self.runner.invoke(cli_module.main, ["env", "update"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not initialized with environment tracking", result.output)


if __name__ == "__main__":
    unittest.main()
