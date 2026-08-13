# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from build_tools.ci import check_release_tree


class CheckReleaseTreeTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name)
        self._git("init", "--quiet")
        self._git("config", "user.email", "release-tree-test@example.com")
        self._git("config", "user.name", "Release Tree Test")
        hooks_path = self.repository_root / ".git" / "test-hooks"
        hooks_path.mkdir()
        self._git("config", "core.hooksPath", str(hooks_path))

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            capture_output=True,
            check=True,
            cwd=self.repository_root,
            text=True,
        )
        return result.stdout

    def _write(self, relative_path: str, contents: str) -> Path:
        path = self.repository_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def test_accepts_tree_without_tracked_experimental_paths(self):
        self._write("kernel/example/kernel.loom", "module {\n}\n")
        self._git("add", "--all")

        self.assertEqual(
            check_release_tree.tracked_experimental_paths(self.repository_root),
            [],
        )

    def test_rejects_every_tracked_experimental_path(self):
        self._write("experimental/zeta/README.md", "# Zeta\n")
        self._write("experimental/alpha/kernel.loom", "module {\n}\n")
        self._git("add", "--all")

        self.assertEqual(
            check_release_tree.tracked_experimental_paths(self.repository_root),
            [
                "experimental/alpha/kernel.loom",
                "experimental/zeta/README.md",
            ],
        )

    def test_ignores_untracked_experimental_work(self):
        self._write("experimental/model/program.loom", "module {\n}\n")

        self.assertEqual(
            check_release_tree.tracked_experimental_paths(self.repository_root),
            [],
        )

    def test_candidate_tree_may_remove_paths_present_in_history(self):
        experimental_path = self._write(
            "experimental/model/program.loom", "module {\n}\n"
        )
        self._write("kernel/example/kernel.loom", "module {\n}\n")
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "Branch experiment")

        experimental_path.unlink()
        self._git("add", "--all")

        self.assertEqual(
            check_release_tree.tracked_experimental_paths(self.repository_root),
            [],
        )

    def test_main_reports_promotion_boundary(self):
        self._write("experimental/model/program.loom", "module {\n}\n")
        self._git("add", "--all")
        error_output = io.StringIO()

        with (
            mock.patch.object(
                check_release_tree, "REPOSITORY_ROOT", self.repository_root
            ),
            redirect_stderr(error_output),
        ):
            result = check_release_tree.main()

        self.assertEqual(result, 1)
        self.assertIn(
            "experimental/model/program.loom",
            error_output.getvalue(),
        )
        self.assertIn("Promote reusable work", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
