# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
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
        (source_root / "bazel-bin").symlink_to("/tmp/bazel-output")
        reused_root, reused_identity = compile_report.materialize_base_source(
            self.repository_root,
            output_root,
            commit,
        )

        self.assertEqual(source_root, reused_root)
        self.assertEqual(identity["commit"], commit)
        self.assertEqual(reused_identity["commit"], commit)
        self.assertEqual(identity, reused_identity)
        self.assertEqual(
            (source_root / ".bazelrc.local").read_text(encoding="utf-8"),
            "common --override_module=iree=/loom\n",
        )
        self.assertFalse((source_root / ".git").exists())

        (source_root / "BUILD.bazel").write_text("# modified\n", encoding="utf-8")
        with self.assertRaisesRegex(compile_report.Error, "was modified"):
            compile_report.materialize_base_source(
                self.repository_root,
                output_root,
                "HEAD",
            )


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
        catalog = (
            "//kernel/example:first_compile\n//motif/example/test:second_compile\n"
        )
        with mock.patch.object(
            compile_report, "_bazel_output", side_effect=[catalog, output]
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
        query_arguments = bazel_output.call_args_list[1].args[2]
        self.assertEqual(query_arguments[0], "cquery")
        self.assertEqual(
            query_arguments[1],
            'rdeps(//..., set("//kernel/..." "//motif/..."))',
        )
        self.assertIn("%LoomCompilationInfo", query_arguments[-1])

    def test_missing_pattern_produces_empty_side_of_comparison(self):
        with mock.patch.object(
            compile_report, "_bazel_output", return_value="//kernel:present\n"
        ) as bazel_output:
            compilations = compile_report.discover_compilations(
                Path("/repository"),
                ["//kernel:missing"],
                ["-c", "opt"],
                "bazelisk",
            )

        self.assertEqual(compilations, [])
        bazel_output.assert_called_once()

    def test_shorthand_label_resolves_through_public_target_catalog(self):
        with mock.patch.object(
            compile_report,
            "_bazel_output",
            side_effect=[
                "//kernel/example:example\n",
                (
                    "@@//kernel/example:example_compile"
                    "\tbazel-out/bin/example.hsaco"
                    "\tbazel-out/bin/example.compile.json"
                    "\tamdgpu-hal\tgfx11-generic\n"
                ),
            ],
        ) as bazel_output:
            compile_report.discover_compilations(
                Path("/repository"),
                ["//kernel/example"],
                ["-c", "opt"],
                "bazelisk",
            )

        self.assertEqual(
            bazel_output.call_args_list[1].args[2][1],
            'rdeps(//..., set("//kernel/example:example"))',
        )


class ReportToolTest(unittest.TestCase):
    def test_identity_covers_project_owned_implementation_runfiles(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            execution_root = Path(temporary_directory) / "execroot"
            relative_executable = PurePosixPath(
                "bazel-out/bin/external/iree+/loom/tools/loom-compile-report"
            )
            executable = execution_root.joinpath(*relative_executable.parts)
            implementation = Path(
                str(executable) + ".runfiles/iree+/loom/reporting/report.py"
            )
            executable.parent.mkdir(parents=True)
            implementation.parent.mkdir(parents=True)
            executable.write_bytes(b"launcher")
            implementation.write_bytes(b"implementation one")
            target_files = (
                f"executable\t{relative_executable}\n"
                "runfile\texternal/iree+/loom/reporting/report.py\n"
                "runfile\texternal/python/runtime\n"
            )

            def bazel_output(unused_bazel, unused_root, arguments):
                if arguments[0] == "cquery":
                    return target_files
                if arguments[0] == "info":
                    return str(execution_root) + "\n"
                self.fail(f"Unexpected Bazel arguments: {arguments}")

            with (
                mock.patch.object(compile_report, "_bazel"),
                mock.patch.object(
                    compile_report, "_bazel_output", side_effect=bazel_output
                ),
            ):
                first = compile_report.resolve_report_tool(
                    Path("/repository"), ["-c", "opt"], "bazelisk"
                )
                implementation.write_bytes(b"implementation two")
                second = compile_report.resolve_report_tool(
                    Path("/repository"), ["-c", "opt"], "bazelisk"
                )

        self.assertEqual(first.path, executable)
        self.assertNotEqual(first.sha256, second.sha256)

    def test_show_view_uses_capture_relative_source_path(self):
        capture_root = Path("/evidence/captures/capture")
        report_path = capture_root / "reports/kernel/example.compile.json"
        report_tool = compile_report.ReportTool(Path("/tools/report"), "identity")
        with mock.patch.object(
            compile_report,
            "_command_output",
            return_value=json.dumps(
                {"entries": [], "kind": "loom.compile_report.show"}
            ),
        ) as command_output:
            compile_report._show_report(report_tool, report_path, capture_root)

        command_output.assert_called_once_with(
            [
                "/tools/report",
                "show",
                "reports/kernel/example.compile.json",
                "--format=json",
            ],
            cwd=capture_root,
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

    def test_rejects_empty_base_before_initializing_workspace(self):
        args = SimpleNamespace(
            base="",
            configs=[],
            output_dir=".notes/compile-reports",
            targets=[],
        )
        with (
            mock.patch.object(compile_report, "initialize_workspace") as initialize,
            self.assertRaisesRegex(compile_report.Error, "must not be empty"),
        ):
            compile_report.run(
                args,
                repository_root=Path("/repository"),
                bazel_executable="bazelisk",
            )

        initialize.assert_not_called()


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.output_root = Path(self.temporary_directory.name)
        self.report_tool_path = self.output_root / "loom-compile-report"
        self.report_tool_path.write_bytes(b"report tool")
        self.report_tool = compile_report.ReportTool(
            path=self.report_tool_path,
            sha256=compile_report._sha256(self.report_tool_path),
        )

    def _capture(
        self,
        capture_id: str,
        entries: list[tuple[str, bytes, bytes]],
    ) -> dict[str, object]:
        capture_root = self.output_root / "captures" / capture_id
        manifest_entries = []
        for index, (label, artifact, report) in enumerate(entries):
            artifact_relative = f"artifacts/{index}.bin"
            report_relative = f"reports/{index}.json"
            artifact_path = capture_root / artifact_relative
            report_path = capture_root / report_relative
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(artifact)
            report_path.write_bytes(report)
            manifest_entries.append(
                {
                    "artifact": {
                        "path": artifact_relative,
                        "sha256": compile_report._sha256(artifact_path),
                    },
                    "backend": "amdgpu-hal",
                    "label": label,
                    "report": {
                        "path": report_relative,
                        "sha256": compile_report._sha256(report_path),
                    },
                    "target": "gfx11-generic",
                }
            )
        return {"capture_id": capture_id, "entries": manifest_entries}

    def test_digest_gate_preserves_added_removed_and_unchanged_entries(self):
        base = self._capture(
            "base",
            [
                ("//kernel/example:removed", b"removed", b"removed report"),
                ("//kernel/example:stable", b"stable", b"stable report"),
            ],
        )
        candidate = self._capture(
            "candidate",
            [
                ("//kernel/example:added", b"added", b"added report"),
                ("//kernel/example:stable", b"stable", b"stable report"),
            ],
        )

        with mock.patch.object(compile_report, "_run_report_tool") as report_tool:
            comparison = compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )

        report_tool.assert_not_called()
        self.assertEqual(
            [(entry["label"], entry["state"]) for entry in comparison["entries"]],
            [
                ("//kernel/example:added", "added"),
                ("//kernel/example:removed", "removed"),
                ("//kernel/example:stable", "unchanged"),
            ],
        )
        self.assertEqual(comparison["counts"]["common"], 1)
        self.assertEqual(comparison["counts"]["unchanged"], 1)

    def test_changed_pair_emits_public_diff_views_and_metric_summary(self):
        label = "//kernel/example:changed"
        base = self._capture("base", [(label, b"old artifact", b"old report")])
        candidate = self._capture(
            "candidate", [(label, b"new artifact", b"new report")]
        )
        diff_view = {
            "changed_entry_count": 1,
            "entries": [
                {
                    "artifact_facts": {
                        "changed": {
                            "code_byte_count": {
                                "baseline": 512,
                                "candidate": 480,
                                "delta": -32,
                            }
                        },
                        "incomplete": {},
                    },
                    "compiler_analysis": {
                        "changed": {
                            "vector_register_count": {
                                "baseline": 32,
                                "candidate": 28,
                                "delta": -4,
                            }
                        },
                        "incomplete": {},
                    },
                    "identity": {"name": "changed"},
                }
            ],
            "kind": "loom.compile_report.diff",
            "unchanged_entry_count": 0,
        }
        results = [
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(diff_view) + "\n", stderr=""
            ),
            subprocess.CompletedProcess(
                [], 0, stdout="Loom compile report diff\n", stderr=""
            ),
        ]
        with mock.patch.object(
            compile_report, "_run_report_tool", side_effect=results
        ) as report_tool:
            comparison = compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )
            reused = compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )

        self.assertEqual(comparison, reused)
        self.assertEqual(report_tool.call_count, 2)
        entry = comparison["entries"][0]
        self.assertEqual(entry["state"], "changed")
        self.assertEqual(entry["semantic_state"], "changed")
        self.assertEqual(
            [(metric["metric"], metric["delta"]) for metric in entry["metrics"]],
            [("code_byte_count", -32), ("vector_register_count", -4)],
        )
        comparison_root = self.output_root / "comparisons" / comparison["comparison_id"]
        self.assertTrue((comparison_root / entry["diff_json"]["path"]).is_file())
        summary_text = (comparison_root / "summary.txt").read_text(encoding="utf-8")
        self.assertIn("code_byte_count: 512 -> 480 (-32)", summary_text)

    def test_artifact_only_change_records_unchanged_public_evidence(self):
        label = "//kernel/example:artifact_only"
        base = self._capture("base", [(label, b"old artifact", b"same report")])
        candidate = self._capture(
            "candidate", [(label, b"new artifact", b"same report")]
        )
        diff_view = {
            "changed_entry_count": 0,
            "entries": [],
            "kind": "loom.compile_report.diff",
            "unchanged_entry_count": 1,
        }
        results = [
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(diff_view) + "\n", stderr=""
            ),
            subprocess.CompletedProcess([], 0, stdout="unchanged\n", stderr=""),
        ]
        with mock.patch.object(compile_report, "_run_report_tool", side_effect=results):
            comparison = compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )

        entry = comparison["entries"][0]
        self.assertEqual(entry["semantic_state"], "unchanged")
        self.assertEqual(comparison["counts"]["semantically_unchanged"], 1)

    def test_incomparable_pair_preserves_public_tool_failure(self):
        label = "//kernel/example:incomparable"
        base = self._capture("base", [(label, b"old", b"old report")])
        candidate = self._capture("candidate", [(label, b"new", b"new report")])
        with mock.patch.object(
            compile_report,
            "_run_report_tool",
            return_value=subprocess.CompletedProcess(
                [], 2, stdout="", stderr="identity mismatch\n"
            ),
        ):
            comparison = compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )

        entry = comparison["entries"][0]
        self.assertEqual(entry["state"], "incomparable")
        self.assertEqual(comparison["counts"]["incomparable"], 1)
        stderr_path = (
            self.output_root
            / "comparisons"
            / comparison["comparison_id"]
            / entry["stderr"]["path"]
        )
        self.assertEqual(stderr_path.read_text(encoding="utf-8"), "identity mismatch\n")

    def test_rejects_corrupt_comparison_sidecar(self):
        label = "//kernel/example:changed"
        base = self._capture("base", [(label, b"old", b"old report")])
        candidate = self._capture("candidate", [(label, b"new", b"new report")])
        diff_view = {
            "changed_entry_count": 0,
            "entries": [],
            "kind": "loom.compile_report.diff",
            "unchanged_entry_count": 1,
        }
        with mock.patch.object(
            compile_report,
            "_run_report_tool",
            side_effect=[
                subprocess.CompletedProcess(
                    [], 0, stdout=json.dumps(diff_view), stderr=""
                ),
                subprocess.CompletedProcess([], 0, stdout="unchanged\n", stderr=""),
            ],
        ):
            comparison = compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )
        entry = comparison["entries"][0]
        sidecar_path = (
            self.output_root
            / "comparisons"
            / comparison["comparison_id"]
            / entry["diff_text"]["path"]
        )
        sidecar_path.write_text("corrupt\n", encoding="utf-8")

        with self.assertRaisesRegex(compile_report.Error, "Corrupt comparison"):
            compile_report.compare_captures(
                self.output_root, base, candidate, self.report_tool
            )


if __name__ == "__main__":
    unittest.main()
