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


class CliTask001Tests(unittest.TestCase):
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
        cl9_dir.mkdir()
        with open(cl9_dir / "config.json", "w") as f:
            json.dump({"name": name, "version": "1"}, f)

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

    def test_init_dot_uses_directory_name(self):
        project_dir = self.work_dir / "demo"
        project_dir.mkdir()
        self._chdir(project_dir)

        result = self.runner.invoke(cli_module.main, ["init", "."])

        self.assertEqual(result.exit_code, 0)
        with open(project_dir / ".cl9" / "config.json", "r") as f:
            project_config = json.load(f)

        self.assertEqual(project_config["name"], "demo")
        self.assertEqual(self.test_config.get_project("demo")["path"], str(project_dir.resolve()))

    def test_init_path_accepts_explicit_name(self):
        project_dir = self.work_dir / "source-project"
        project_dir.mkdir()

        result = self.runner.invoke(
            cli_module.main,
            ["init", str(project_dir), "--name", "alias-project"],
        )

        self.assertEqual(result.exit_code, 0)
        with open(project_dir / ".cl9" / "config.json", "r") as f:
            project_config = json.load(f)

        self.assertEqual(project_config["name"], "alias-project")
        self.assertEqual(
            self.test_config.get_project("alias-project")["path"],
            str(project_dir.resolve()),
        )

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
        registered_dir = self.work_dir / "registered-target"
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

    def test_enter_name_flag_requires_registry_lookup(self):
        local_path = self.work_dir / "foo"
        local_path.mkdir()
        self._write_local_project_config(local_path, "local-foo")
        self._chdir(self.work_dir)

        result = self.runner.invoke(cli_module.main, ["enter", "--name", "foo"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not found in registry", result.output)

    def test_enter_smart_mode_reports_unresolved_target(self):
        self._chdir(self.work_dir)

        result = self.runner.invoke(cli_module.main, ["enter", "missing-target"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Could not resolve 'missing-target'", result.output)


if __name__ == "__main__":
    unittest.main()
