# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from build_tools import compile_report


class GitRepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name) / "repository"
        self.repository_root.mkdir()
        self._git("init", "--quiet")
        self._git("config", "user.email", "compile-report-test@example.com")
        self._git("config", "user.name", "Compile Report Test")
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

    def _commit_all(self) -> str:
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "Test baseline")
        return self._git("rev-parse", "HEAD").strip()


class WorkingTreeIdentityTest(GitRepositoryTestCase):
    def test_identity_tracks_final_dirty_contents(self):
        source = self._write("kernel/example.loom", "baseline\n")
        self._commit_all()

        clean = compile_report.working_tree_identity(self.repository_root)
        self.assertFalse(clean["dirty"])

        source.write_text("candidate one\n", encoding="utf-8")
        untracked = self._write("motif/new.loom", "new motif\n")
        first = compile_report.working_tree_identity(self.repository_root)
        source.write_text("candidate two\n", encoding="utf-8")
        second = compile_report.working_tree_identity(self.repository_root)

        self.assertTrue(first["dirty"])
        self.assertEqual(
            first["dirty_paths"],
            ["kernel/example.loom", "motif/new.loom"],
        )
        self.assertNotEqual(first["tree_sha256"], second["tree_sha256"])
        self.assertTrue(untracked.is_file())


class BaseSourceTest(GitRepositoryTestCase):
    def test_materializes_one_source_workspace_per_commit_and_local_config(self):
        self._write("BUILD.bazel", "# base\n")
        self._write(".bazelrc.local", "common --override_module=iree=/loom\n")
        commit = self._commit_all()
        output_root = self.repository_root / ".notes" / "compile-reports"
        compile_report.initialize_workspace(self.repository_root, output_root)

        source_root, identity = compile_report.materialize_base_source(
            self.repository_root,
            output_root,
            "HEAD",
        )
        reused_root, reused_identity = compile_report.materialize_base_source(
            self.repository_root,
            output_root,
            commit,
        )

        self.assertEqual(source_root, reused_root)
        self.assertEqual(identity["commit"], commit)
        self.assertEqual(reused_identity["commit"], commit)
        self.assertEqual(identity["reference"], "HEAD")
        self.assertEqual(reused_identity["reference"], commit)
        self.assertEqual(
            (source_root / ".bazelrc.local").read_text(encoding="utf-8"),
            "common --override_module=iree=/loom\n",
        )
        self.assertFalse((source_root / ".git").exists())


class ProviderDiscoveryTest(unittest.TestCase):
    def test_discovers_only_public_compilation_provider_rows(self):
        output = (
            "\n"
            "@@//kernel/example:first_compile"
            "\tbazel-out/bin/kernel/example/first.hsaco"
            "\tbazel-out/bin/kernel/example/first.compile.json"
            "\tamdgpu-hal\tgfx11-generic\n"
            "\n"
            "@@//motif/example/test:second_compile"
            "\tbazel-out/bin/motif/example/test/second.spv"
            "\tbazel-out/bin/motif/example/test/second.compile.json"
            "\tspirv-hal\tvulkan-spirv\n"
        )
        with mock.patch.object(
            compile_report, "_bazel_output", return_value=output
        ) as bazel_output:
            compilations = compile_report.discover_compilations(
                Path("/repository"),
                ["//kernel/...", "//motif/..."],
                ["-c", "opt", "--config=amdgpu"],
                "bazelisk",
            )

        self.assertEqual(
            compilations,
            [
                compile_report.Compilation(
                    label="//kernel/example:first_compile",
                    artifact_path=PurePosixPath(
                        "bazel-out/bin/kernel/example/first.hsaco"
                    ),
                    report_path=PurePosixPath(
                        "bazel-out/bin/kernel/example/first.compile.json"
                    ),
                    backend="amdgpu-hal",
                    target="gfx11-generic",
                ),
                compile_report.Compilation(
                    label="//motif/example/test:second_compile",
                    artifact_path=PurePosixPath(
                        "bazel-out/bin/motif/example/test/second.spv"
                    ),
                    report_path=PurePosixPath(
                        "bazel-out/bin/motif/example/test/second.compile.json"
                    ),
                    backend="spirv-hal",
                    target="vulkan-spirv",
                ),
            ],
        )
        query_arguments = bazel_output.call_args.args[2]
        self.assertEqual(query_arguments[0], "cquery")
        self.assertEqual(
            query_arguments[1],
            'set("//kernel/..." "//motif/...")',
        )
        self.assertIn("%LoomCompilationInfo", query_arguments[-1])

    def test_rejects_empty_provider_corpus(self):
        with (
            mock.patch.object(compile_report, "_bazel_output", return_value="\n"),
            self.assertRaisesRegex(compile_report.Error, "No Loom compilations"),
        ):
            compile_report.discover_compilations(
                Path("/repository"), [], ["-c", "opt"], "bazelisk"
            )


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary_root = Path(self.temporary_directory.name)
        self.repository_root = temporary_root / "repository"
        self.repository_root.mkdir()
        self.output_root = self.repository_root / ".notes" / "compile-reports"
        compile_report.initialize_workspace(self.repository_root, self.output_root)
        self.execution_root = temporary_root / "execution-root"
        self.artifact_path = self.execution_root / "bazel-out/bin/kernel/example.hsaco"
        self.report_path = (
            self.execution_root / "bazel-out/bin/kernel/example.compile.json"
        )
        self.artifact_path.parent.mkdir(parents=True)
        self.artifact_path.write_bytes(b"artifact one")
        self.report_path.write_text('{"report": 1}\n', encoding="utf-8")
        self.report_tool_path = temporary_root / "loom-compile-report"
        self.report_tool_path.write_bytes(b"report tool")
        self.report_tool = compile_report.ReportTool(
            path=self.report_tool_path,
            sha256=compile_report._sha256(self.report_tool_path),
        )
        self.compilation = compile_report.Compilation(
            label="//kernel:example_compile",
            artifact_path=PurePosixPath("bazel-out/bin/kernel/example.hsaco"),
            report_path=PurePosixPath("bazel-out/bin/kernel/example.compile.json"),
            backend="amdgpu-hal",
            target="gfx11-generic",
        )

    def test_materializes_package_shaped_capture_and_reuses_exact_identity(self):
        show_view = {
            "entries": [{"identity": {"name": "example"}}],
            "kind": "loom.compile_report.show",
        }
        with (
            mock.patch.object(
                compile_report,
                "discover_compilations",
                return_value=[self.compilation],
            ),
            mock.patch.object(
                compile_report,
                "build_compilations",
                return_value=self.execution_root,
            ),
            mock.patch.object(
                compile_report, "_show_report", return_value=show_view
            ) as show_report,
        ):
            first = compile_report.capture_repository(
                self.repository_root,
                self.output_root,
                {"type": "working_tree", "tree_sha256": "candidate"},
                ["//..."],
                ["amdgpu"],
                self.report_tool,
                "bazelisk",
            )
            second = compile_report.capture_repository(
                self.repository_root,
                self.output_root,
                {"type": "working_tree", "tree_sha256": "candidate"},
                ["//..."],
                ["amdgpu"],
                self.report_tool,
                "bazelisk",
            )

        self.assertEqual(first, second)
        self.assertEqual(show_report.call_count, 1)
        capture_root = self.output_root / "captures" / first["capture_id"]
        entry = first["entries"][0]
        self.assertEqual(entry["label"], "//kernel:example_compile")
        self.assertTrue((capture_root / entry["artifact"]["path"]).is_file())
        self.assertTrue((capture_root / entry["report"]["path"]).is_file())
        self.assertTrue((capture_root / entry["view"]["path"]).is_file())

    def test_capture_identity_changes_with_final_artifact_bytes(self):
        with (
            mock.patch.object(
                compile_report,
                "discover_compilations",
                return_value=[self.compilation],
            ),
            mock.patch.object(
                compile_report,
                "build_compilations",
                return_value=self.execution_root,
            ),
            mock.patch.object(
                compile_report,
                "_show_report",
                return_value={
                    "entries": [],
                    "kind": "loom.compile_report.show",
                },
            ),
        ):
            first = compile_report.capture_repository(
                self.repository_root,
                self.output_root,
                {"type": "working_tree", "tree_sha256": "candidate"},
                [],
                [],
                self.report_tool,
                "bazelisk",
            )
            self.artifact_path.write_bytes(b"artifact two")
            second = compile_report.capture_repository(
                self.repository_root,
                self.output_root,
                {"type": "working_tree", "tree_sha256": "candidate"},
                [],
                [],
                self.report_tool,
                "bazelisk",
            )

        self.assertNotEqual(first["capture_id"], second["capture_id"])

    def test_rejects_corrupt_immutable_capture(self):
        with (
            mock.patch.object(
                compile_report,
                "discover_compilations",
                return_value=[self.compilation],
            ),
            mock.patch.object(
                compile_report,
                "build_compilations",
                return_value=self.execution_root,
            ),
            mock.patch.object(
                compile_report,
                "_show_report",
                return_value={
                    "entries": [],
                    "kind": "loom.compile_report.show",
                },
            ),
        ):
            capture = compile_report.capture_repository(
                self.repository_root,
                self.output_root,
                {"type": "working_tree", "tree_sha256": "candidate"},
                [],
                [],
                self.report_tool,
                "bazelisk",
            )
            capture_root = self.output_root / "captures" / capture["capture_id"]
            report_path = capture_root / capture["entries"][0]["report"]["path"]
            report_path.write_text("corrupt\n", encoding="utf-8")

            with self.assertRaisesRegex(compile_report.Error, "Corrupt report"):
                compile_report.capture_repository(
                    self.repository_root,
                    self.output_root,
                    {"type": "working_tree", "tree_sha256": "candidate"},
                    [],
                    [],
                    self.report_tool,
                    "bazelisk",
                )


class WorkspaceTest(unittest.TestCase):
    def test_rejects_unignored_workspace_inside_repository(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            with self.assertRaisesRegex(compile_report.Error, "below .notes"):
                compile_report.initialize_workspace(
                    repository_root,
                    repository_root / "compile-reports",
                )


if __name__ == "__main__":
    unittest.main()
