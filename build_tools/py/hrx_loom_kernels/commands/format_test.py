# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from build_tools.py.hrx_loom_kernels.commands import format as format_command
from build_tools.py.hrx_loom_kernels.context import RepositoryContext, UserError


class LoomFormatCommandTest(unittest.TestCase):
    def test_default_discovery_includes_experimental_sources(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            experimental_source = repository_root / "experimental/model/program.loom"
            kernel_source = repository_root / "kernel/example/kernel.loom"
            ignored_source = repository_root / "scratch/ignored.loom"
            for source in (experimental_source, kernel_source, ignored_source):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("module {\n}\n", encoding="utf-8")

            sources = format_command._loom_sources(repository_root, [])

        self.assertEqual(
            sources,
            sorted([experimental_source.resolve(), kernel_source.resolve()]),
        )

    def test_runs_one_batch_formatter_command(self):
        sources = [Path("/repo/motif/a.loom"), Path("/repo/motif/b.loom")]
        context = mock.Mock(spec=RepositoryContext)

        format_command._run_loom_format(context, "--check", sources)

        context.bazel.assert_called_once_with(
            [
                "run",
                format_command.LOOM_FORMAT_TARGET,
                "--",
                "--check",
                "/repo/motif/a.loom",
                "/repo/motif/b.loom",
            ]
        )

    def test_format_defaults_to_fix_mode(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        format_command.register(subparsers)

        self.assertEqual(parser.parse_args(["format"]).mode, "fix")
        self.assertEqual(parser.parse_args(["format", "--fix"]).mode, "fix")
        self.assertEqual(parser.parse_args(["format", "--check"]).mode, "check")

    def test_check_mode_is_read_only(self):
        source = Path("/repo/motif/a.loom")
        args = SimpleNamespace(mode="check", sources=[str(source)], staged=False)
        context = mock.Mock(spec=RepositoryContext)
        context.repository_root = Path("/repo")

        with (
            mock.patch.object(format_command, "_loom_sources", return_value=[source]),
            mock.patch.object(format_command, "_run_loom_format") as run_formatter,
            mock.patch.object(format_command, "_stage_loom_sources") as stage_sources,
        ):
            format_command._run(args, context)

        run_formatter.assert_called_once_with(context, "--check", [source])
        stage_sources.assert_not_called()


class StagedLoomFormatTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name)
        self.context = RepositoryContext(self.repository_root)
        self._git("init", "--quiet")
        self._git("config", "user.email", "loom-format-test@example.com")
        self._git("config", "user.name", "Loom Format Test")
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

    def _commit_all(self) -> None:
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "Test baseline")

    def _format_args(self, *, mode: str = "fix") -> SimpleNamespace:
        return SimpleNamespace(mode=mode, sources=[], staged=True)

    def test_selects_added_and_modified_sources_but_not_deletions(self):
        kept_source = self._write("motif/kept.loom", "base\n")
        deleted_source = self._write("motif/deleted.loom", "delete me\n")
        readme = self._write("README.md", "base\n")
        self._commit_all()

        kept_source.write_text("modified\n", encoding="utf-8")
        deleted_source.unlink()
        readme.write_text("modified\n", encoding="utf-8")
        new_source = self._write("kernel/new.loom", "new\n")
        experimental_source = self._write("experimental/model/new.loom", "experiment\n")
        self._git("add", "--all")

        self.assertEqual(
            format_command._staged_loom_sources(self.context),
            sorted(
                [
                    experimental_source.resolve(),
                    kept_source.resolve(),
                    new_source.resolve(),
                ]
            ),
        )

    def test_fix_refuses_partially_staged_source_without_mutation(self):
        source = self._write("motif/example.loom", "base\n")
        self._commit_all()
        source.write_text("staged\n", encoding="utf-8")
        self._git("add", "motif/example.loom")
        source.write_text("unstaged\n", encoding="utf-8")

        with (
            mock.patch.object(format_command, "_run_loom_format") as run_formatter,
            self.assertRaisesRegex(UserError, "partially staged Loom sources"),
        ):
            format_command._run(self._format_args(), self.context)

        run_formatter.assert_not_called()
        self.assertEqual(source.read_text(encoding="utf-8"), "unstaged\n")
        self.assertEqual(self._git("show", ":motif/example.loom"), "staged\n")

    def test_fix_formats_restages_and_checks_exact_staged_sources(self):
        source = self._write("motif/example.loom", "base\n")
        self._commit_all()
        source.write_text("not canonical\n", encoding="utf-8")
        self._git("add", "motif/example.loom")
        formatter_calls = []

        def run_formatter(context, mode, sources):
            self.assertIs(context, self.context)
            formatter_calls.append((mode, tuple(sources)))
            if mode == "--in-place":
                sources[0].write_text("canonical\n", encoding="utf-8")
            else:
                self.assertEqual(sources[0].read_text(encoding="utf-8"), "canonical\n")

        with mock.patch.object(
            format_command, "_run_loom_format", side_effect=run_formatter
        ):
            format_command._run(self._format_args(), self.context)

        expected_sources = (source.resolve(),)
        self.assertEqual(
            formatter_calls,
            [
                ("--in-place", expected_sources),
                ("--check", expected_sources),
            ],
        )
        self.assertEqual(source.read_text(encoding="utf-8"), "canonical\n")
        self.assertEqual(self._git("show", ":motif/example.loom"), "canonical\n")
        self.assertEqual(self._git("diff", "--name-only"), "")

    def test_empty_staged_selection_skips_formatter(self):
        self._write("README.md", "documentation\n")
        self._git("add", "README.md")

        with mock.patch.object(format_command, "_run_loom_format") as run_formatter:
            format_command._run(self._format_args(), self.context)

        run_formatter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
