import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cl9.mounts as mounts_module
import cl9.profiles as profiles_module


def _run_git(args, cwd):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_bare_profile_repo(path: Path, profile_name: str = "testprof") -> Path:
    """Create a git repo on disk with a single profile under profiles/<name>/."""
    path.mkdir(parents=True)
    _run_git(["init", "-q", "-b", "main"], cwd=path)
    _run_git(["config", "user.email", "test@example.com"], cwd=path)
    _run_git(["config", "user.name", "Test"], cwd=path)

    profile_dir = path / "profiles" / profile_name
    profile_dir.mkdir(parents=True)
    with open(profile_dir / "manifest.json", "w") as f:
        json.dump({"tool": "claude", "executable": "claude"}, f)
    with open(profile_dir / "CLAUDE.md", "w") as f:
        f.write(f"# {profile_name}\n")

    _run_git(["add", "."], cwd=path)
    _run_git(["commit", "-q", "-m", "init"], cwd=path)
    return path


class MountsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.base = Path(self.tmpdir.name)

        self.mounts_dir = self.base / "mounts"
        self._patches = [
            patch.object(mounts_module, "MOUNTS_DIR", self.mounts_dir),
            patch.object(profiles_module, "MOUNTS_DIR", self.mounts_dir),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_infer_mount_name(self):
        cases = {
            "https://github.com/foo/cl9-profiles.git": "cl9-profiles",
            "https://github.com/foo/cl9-profiles": "cl9-profiles",
            "git@github.com:foo/bar.git": "bar",
            "/tmp/baz": "baz",
            "file:///tmp/qux.git": "qux",
            "repo.git": "repo",
            "repo": "repo",
            # Ref suffixes should be stripped before inferring the name.
            "https://github.com/foo/bar@v1.2.3": "bar",
            "git@github.com:foo/bar@main": "bar",
            "/tmp/baz@abc1234": "baz",
        }
        for url, expected in cases.items():
            self.assertEqual(mounts_module.infer_mount_name(url), expected, url)

    def test_parse_mount_spec(self):
        cases = [
            # No ref.
            ("/tmp/repo", ("/tmp/repo", None)),
            ("https://github.com/foo/bar", ("https://github.com/foo/bar", None)),
            ("https://github.com/foo/bar.git", ("https://github.com/foo/bar.git", None)),
            # SSH URL without a ref: the git@host:path '@' must not be mistaken
            # for a ref separator.
            ("git@github.com:foo/bar", ("git@github.com:foo/bar", None)),
            ("git@github.com:foo/bar.git", ("git@github.com:foo/bar.git", None)),
            # With refs.
            ("/tmp/repo@main", ("/tmp/repo", "main")),
            ("https://github.com/foo/bar@v1.2.3", ("https://github.com/foo/bar", "v1.2.3")),
            ("git@github.com:foo/bar@v1.2.3", ("git@github.com:foo/bar", "v1.2.3")),
            ("git@github.com:foo/bar.git@abc1234", ("git@github.com:foo/bar.git", "abc1234")),
            # Refs that contain '/' (e.g. feature branches) are still fine.
            ("/tmp/repo@feature/x", ("/tmp/repo", "feature/x")),
        ]
        for spec, expected in cases:
            self.assertEqual(mounts_module.parse_mount_spec(spec), expected, spec)

    def test_add_mount_clones_repo_and_counts_contents(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")

        info = mounts_module.add_mount(str(source))

        self.assertEqual(info.name, "upstream")
        self.assertEqual(info.path, self.mounts_dir / "upstream")
        self.assertTrue(info.path.is_dir())
        self.assertTrue((info.path / "profiles" / "alpha" / "manifest.json").is_file())
        self.assertEqual(info.profile_count, 1)
        self.assertEqual(info.mcp_count, 0)
        self.assertEqual(info.skill_count, 0)
        self.assertIsNone(info.ref)

    def test_add_mount_with_ref_branch(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")

        # Create a second branch with an extra profile and switch back to main.
        _run_git(["checkout", "-q", "-b", "feat"], cwd=source)
        extra = source / "profiles" / "beta"
        extra.mkdir()
        with open(extra / "manifest.json", "w") as f:
            json.dump({"tool": "claude"}, f)
        with open(extra / "CLAUDE.md", "w") as f:
            f.write("# beta\n")
        _run_git(["add", "."], cwd=source)
        _run_git(["commit", "-q", "-m", "add beta on feat"], cwd=source)
        _run_git(["checkout", "-q", "main"], cwd=source)

        info = mounts_module.add_mount(f"{source}@feat", name="pinned")

        self.assertEqual(info.ref, "feat")
        self.assertEqual(info.profile_count, 2)  # main's alpha + feat's beta

    def test_add_mount_with_ref_commit_sha(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")
        # Capture the SHA of the only commit.
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(source),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Add a second commit upstream so HEAD moves.
        later = source / "profiles" / "beta"
        later.mkdir()
        with open(later / "manifest.json", "w") as f:
            json.dump({"tool": "claude"}, f)
        with open(later / "CLAUDE.md", "w") as f:
            f.write("# beta\n")
        _run_git(["add", "."], cwd=source)
        _run_git(["commit", "-q", "-m", "add beta"], cwd=source)

        info = mounts_module.add_mount(f"{source}@{sha}", name="frozen")

        self.assertEqual(info.ref, sha)
        # Pinned at the earlier commit, only alpha exists.
        self.assertEqual(info.profile_count, 1)
        self.assertTrue((info.path / "profiles" / "alpha").is_dir())
        self.assertFalse((info.path / "profiles" / "beta").exists())

    def test_add_mount_with_custom_name(self):
        source = _init_bare_profile_repo(self.base / "upstream")
        info = mounts_module.add_mount(str(source), name="custom")
        self.assertEqual(info.name, "custom")
        self.assertTrue((self.mounts_dir / "custom" / "profiles" / "testprof").is_dir())

    def test_add_mount_rejects_duplicate_name(self):
        source = _init_bare_profile_repo(self.base / "upstream")
        mounts_module.add_mount(str(source))
        with self.assertRaises(ValueError):
            mounts_module.add_mount(str(source))

    def test_add_mount_propagates_git_failure(self):
        with self.assertRaises(RuntimeError):
            mounts_module.add_mount("/nonexistent/path/that/should/not/exist", name="broken")

    def test_list_mounts_empty(self):
        self.assertEqual(mounts_module.list_mounts(), [])

    def test_list_mounts_returns_all(self):
        src_a = _init_bare_profile_repo(self.base / "a", profile_name="alpha")
        src_b = _init_bare_profile_repo(self.base / "b", profile_name="beta")
        mounts_module.add_mount(str(src_a), name="aa")
        mounts_module.add_mount(str(src_b), name="bb")

        all_mounts = mounts_module.list_mounts()
        self.assertEqual([m.name for m in all_mounts], ["aa", "bb"])

    def test_remove_mount(self):
        source = _init_bare_profile_repo(self.base / "upstream")
        mounts_module.add_mount(str(source), name="to-remove")
        self.assertTrue((self.mounts_dir / "to-remove").is_dir())

        mounts_module.remove_mount("to-remove")
        self.assertFalse((self.mounts_dir / "to-remove").exists())

    def test_remove_mount_missing(self):
        with self.assertRaises(ValueError):
            mounts_module.remove_mount("nope")

    def test_update_mount_pulls_new_commit(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")
        mounts_module.add_mount(str(source), name="live")

        # Add a second profile upstream and commit
        new_profile = source / "profiles" / "beta"
        new_profile.mkdir()
        with open(new_profile / "manifest.json", "w") as f:
            json.dump({"tool": "claude"}, f)
        with open(new_profile / "CLAUDE.md", "w") as f:
            f.write("# beta\n")
        _run_git(["add", "."], cwd=source)
        _run_git(["commit", "-q", "-m", "add beta"], cwd=source)

        updated = mounts_module.update_mount("live")
        self.assertEqual(updated.profile_count, 2)

    def test_update_mount_discards_local_modifications(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")
        mounts_module.add_mount(str(source), name="live")

        # Scribble on a tracked file inside the mount.
        claude_md = self.mounts_dir / "live" / "profiles" / "alpha" / "CLAUDE.md"
        with open(claude_md, "w") as f:
            f.write("# tampered\n")
        # Drop an untracked file too.
        (self.mounts_dir / "live" / "stowaway.txt").write_text("junk")

        mounts_module.update_mount("live")

        with open(claude_md) as f:
            self.assertEqual(f.read(), "# alpha\n")
        self.assertFalse((self.mounts_dir / "live" / "stowaway.txt").exists())

    def test_update_mount_follows_branch_pin(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")
        mounts_module.add_mount(f"{source}@main", name="tracked")

        # Advance main upstream.
        extra = source / "profiles" / "beta"
        extra.mkdir()
        with open(extra / "manifest.json", "w") as f:
            json.dump({"tool": "claude"}, f)
        with open(extra / "CLAUDE.md", "w") as f:
            f.write("# beta\n")
        _run_git(["add", "."], cwd=source)
        _run_git(["commit", "-q", "-m", "add beta on main"], cwd=source)

        updated = mounts_module.update_mount("tracked")
        self.assertEqual(updated.ref, "main")
        self.assertEqual(updated.profile_count, 2)

    def test_update_mount_commit_pin_is_idempotent(self):
        source = _init_bare_profile_repo(self.base / "upstream", profile_name="alpha")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(source),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        mounts_module.add_mount(f"{source}@{sha}", name="frozen")

        # Advance upstream — pinned mount should not change on update.
        extra = source / "profiles" / "beta"
        extra.mkdir()
        with open(extra / "manifest.json", "w") as f:
            json.dump({"tool": "claude"}, f)
        with open(extra / "CLAUDE.md", "w") as f:
            f.write("# beta\n")
        _run_git(["add", "."], cwd=source)
        _run_git(["commit", "-q", "-m", "add beta"], cwd=source)

        updated = mounts_module.update_mount("frozen")
        self.assertEqual(updated.ref, sha)
        self.assertEqual(updated.profile_count, 1)
        self.assertFalse((updated.path / "profiles" / "beta").exists())

    def test_update_mount_missing(self):
        with self.assertRaises(ValueError):
            mounts_module.update_mount("nope")


class MountedProfileDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.base = Path(self.tmpdir.name)

        self.mounts_dir = self.base / "mounts"
        self.user_dir = self.base / "profiles"
        self._patches = [
            patch.object(mounts_module, "MOUNTS_DIR", self.mounts_dir),
            patch.object(profiles_module, "MOUNTS_DIR", self.mounts_dir),
            patch.object(profiles_module, "USER_PROFILES_DIR", self.user_dir),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _make_profile(self, parent: Path, name: str, contents: str = "body") -> Path:
        profile = parent / name
        profile.mkdir(parents=True)
        with open(profile / "manifest.json", "w") as f:
            json.dump({"tool": "claude"}, f)
        with open(profile / "CLAUDE.md", "w") as f:
            f.write(contents)
        return profile

    def test_mounted_profile_resolves_by_name(self):
        self._make_profile(self.mounts_dir / "pack" / "profiles", "bedrock")
        result = profiles_module.mounted_profile("bedrock")
        self.assertIsNotNone(result)
        spec, mount_name = result
        self.assertEqual(spec.name, "bedrock")
        self.assertEqual(mount_name, "pack")

    def test_user_profile_shadows_mounted(self):
        self._make_profile(self.mounts_dir / "pack" / "profiles", "foo", "from-mount")
        self._make_profile(self.user_dir, "foo", "from-user")

        resolved = profiles_module.resolve_profile("foo")
        self.assertIsNotNone(resolved)
        with open(resolved.claude_md) as f:
            self.assertEqual(f.read(), "from-user")

    def test_mount_shadows_builtin_via_precedence(self):
        # Built-in 'default' exists; put a mount 'default' and verify it wins over builtin.
        self._make_profile(self.mounts_dir / "pack" / "profiles", "default", "from-mount")
        resolved = profiles_module.resolve_profile("default")
        self.assertIsNotNone(resolved)
        with open(resolved.claude_md) as f:
            self.assertEqual(f.read(), "from-mount")

    def test_first_mount_wins_on_collision(self):
        self._make_profile(self.mounts_dir / "aaa" / "profiles", "dup", "from-aaa")
        self._make_profile(self.mounts_dir / "bbb" / "profiles", "dup", "from-bbb")
        result = profiles_module.mounted_profile("dup")
        self.assertIsNotNone(result)
        _, mount_name = result
        self.assertEqual(mount_name, "aaa")

    def test_list_profiles_labels_mount_source(self):
        self._make_profile(self.mounts_dir / "pack" / "profiles", "only-in-mount")
        entries = profiles_module.list_profiles()
        by_name = {spec.name: source for spec, source in entries}
        self.assertIn("only-in-mount", by_name)
        self.assertEqual(by_name["only-in-mount"], "mount:pack")


if __name__ == "__main__":
    unittest.main()
