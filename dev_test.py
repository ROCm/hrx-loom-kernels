# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import dev
from build_tools import benchmark_sweep


class TestCommandTest(unittest.TestCase):
    def test_runs_selected_bazel_target_patterns(self):
        parser = dev._create_parser()
        args = parser.parse_args(
            [
                "test",
                "--target=//kernel/gemm/...",
                "--target=//motif/format/ggml/test/...",
                "--",
                "--config=amdgpu",
            ]
        )

        with (
            mock.patch.object(dev, "_lint_repository") as lint_repository,
            mock.patch.object(dev, "_bazel") as bazel,
        ):
            dev._test(args)

        lint_repository.assert_called_once_with()
        bazel.assert_called_once_with(
            [
                "test",
                "--config=amdgpu",
                "//kernel/gemm/...",
                "//motif/format/ggml/test/...",
            ]
        )

    def test_defaults_to_complete_repository(self):
        args = dev._create_parser().parse_args(["test"])

        with (
            mock.patch.object(dev, "_lint_repository"),
            mock.patch.object(dev, "_bazel") as bazel,
        ):
            dev._test(args)

        bazel.assert_called_once_with(["test", "//..."])


class BenchmarkCommandTest(unittest.TestCase):
    def test_parser_keeps_build_and_runner_configuration_separate(self):
        parser = dev._create_parser()

        args = parser.parse_args(
            [
                "benchmark",
                "--config=amdgpu",
                "--device=amdgpu://0",
                "--output-dir=.notes/benchmarks/gfx1100",
                "--target=//kernel/gemm/...",
                "--target=//motif/format/ggml/test/...",
                "--",
                "--measure=dispatch_complete",
            ]
        )

        self.assertEqual(args.configs, ["amdgpu"])
        self.assertEqual(args.device, "amdgpu://0")
        self.assertEqual(
            args.targets,
            ["//kernel/gemm/...", "//motif/format/ggml/test/..."],
        )
        self.assertFalse(args.rerun_all)
        self.assertEqual(
            args.benchmark_args,
            ["--", "--measure=dispatch_complete"],
        )

    def test_discovery_queries_only_populated_authoring_roots(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            (repository_root / "kernel" / "example").mkdir(parents=True)
            (repository_root / "kernel" / "example" / "BUILD.bazel").write_text(
                "# kernel\n", encoding="utf-8"
            )
            (repository_root / "motif" / "test").mkdir(parents=True)
            (repository_root / "motif" / "test" / "BUILD.bazel").write_text(
                "# motif\n", encoding="utf-8"
            )
            (repository_root / "experimental" / "model").mkdir(parents=True)
            (repository_root / "experimental" / "model" / "BUILD.bazel").write_text(
                "# experiment\n", encoding="utf-8"
            )
            (repository_root / "model").mkdir()

            expression = benchmark_sweep.benchmark_query_expression(repository_root)

        self.assertEqual(
            expression,
            'attr(tags, "loom-benchmark-module", '
            "//experimental/... union //kernel/... union //motif/...)",
        )

    def test_resolves_recursive_and_overlapping_target_patterns(self):
        catalog = [
            "//kernel/gemm/fused:epilogue_benchmark_module",
            "//kernel/gemm:matmul_benchmark_module",
            "//kernel/other:softmax_benchmark_module",
        ]

        def query(arguments, *, capture_output=False):
            self.assertTrue(capture_output)
            self.assertEqual(arguments[0], "query")
            self.assertEqual(arguments[2], "--output=label")
            if arguments[1] == "//kernel/gemm/...":
                return (
                    "//kernel/gemm/fused:epilogue\n"
                    "//kernel/gemm/fused:epilogue_benchmark_module\n"
                    "//kernel/gemm:matmul\n"
                    "//kernel/gemm:matmul_benchmark_module\n"
                )
            if arguments[1] == "//kernel/gemm:matmul":
                return "//kernel/gemm:matmul\n"
            self.fail(f"Unexpected Bazel query: {arguments}")

        with mock.patch.object(
            benchmark_sweep, "discover_targets", return_value=catalog
        ):
            targets = benchmark_sweep.resolve_target_patterns(
                Path("/repo"),
                ["//kernel/gemm:matmul", "//kernel/gemm/..."],
                query,
            )

        self.assertEqual(
            targets,
            [
                "//kernel/gemm/fused:epilogue_benchmark_module",
                "//kernel/gemm:matmul_benchmark_module",
            ],
        )

    def test_reports_target_patterns_without_benchmark_modules(self):
        with (
            mock.patch.object(
                benchmark_sweep,
                "discover_targets",
                return_value=["//kernel/gemm:matmul_benchmark_module"],
            ),
            self.assertRaisesRegex(
                benchmark_sweep.Error,
                "No benchmark modules match Bazel target patterns",
            ),
        ):
            benchmark_sweep.resolve_target_patterns(
                Path("/repo"),
                ["//kernel/attention/..."],
                lambda arguments, capture_output=False: "//kernel/attention:flash\n",
            )

    def test_rejects_empty_target_pattern(self):
        with (
            mock.patch.object(benchmark_sweep, "discover_targets") as discover,
            self.assertRaisesRegex(benchmark_sweep.Error, "must not be empty"),
        ):
            benchmark_sweep.resolve_target_patterns(Path("/repo"), [""], mock.Mock())

        discover.assert_not_called()

    def test_motif_admission_uses_repository_tags_and_dependency_edges(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            for root in ("kernel", "motif"):
                package_root = repository_root / root / "example"
                package_root.mkdir(parents=True)
                (package_root / "BUILD.bazel").write_text("# package\n")

            with mock.patch.object(dev, "REPOSITORY_ROOT", repository_root):
                expression = dev._motif_admission_query_expression()

        self.assertEqual(
            expression,
            'labels(srcs, attr(tags, "loom-admission-motif", //motif/...)) '
            "except "
            'deps(attr(tags, "loom-benchmark-module", '
            "//kernel/... union //motif/...))",
        )

    def test_motif_admission_accepts_complete_qualification_closure(self):
        with mock.patch.object(dev, "_bazel", return_value="") as bazel:
            dev._check_motif_qualification()

        bazel.assert_called_once_with(
            ["query", dev._motif_admission_query_expression(), "--output=label"],
            capture_output=True,
        )

    def test_motif_admission_reports_every_unqualified_library(self):
        with (
            mock.patch.object(
                dev,
                "_bazel",
                return_value=("//motif/format/other:q3\n//motif/format/ggml:q5_k\n"),
            ),
            self.assertRaisesRegex(
                dev.UserError,
                "(?s)//motif/format/ggml:q5_k.*//motif/format/other:q3",
            ),
        ):
            dev._check_motif_qualification()

    def test_resolves_one_materialized_archive_per_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            first_module = repository_root / "bazel-out/bin/first.loombc"
            second_module = repository_root / "bazel-out/bin/second.loombc"
            first_module.parent.mkdir(parents=True)
            first_module.write_bytes(b"first")
            second_module.write_bytes(b"second")
            cquery_output = (
                "@@//kernel/example:first_benchmark_module"
                "\t@@//kernel/example:first\tbazel-out/bin/first.loombc\n"
                "@@//motif/example/test:second\t@@//motif/example/test:second"
                "\tbazel-out/bin/second.loombc\n"
            )

            with mock.patch.object(dev, "_bazel", return_value=cquery_output) as bazel:
                modules = benchmark_sweep.resolve_modules(
                    repository_root,
                    [
                        "//kernel/example:first_benchmark_module",
                        "//motif/example/test:second",
                    ],
                    ["-c", "opt", "--config=amdgpu"],
                    bazel,
                )

        self.assertEqual(
            modules,
            [
                benchmark_sweep.BenchmarkModule(
                    "//kernel/example:first_benchmark_module",
                    "//kernel/example:first",
                    first_module,
                ),
                benchmark_sweep.BenchmarkModule(
                    "//motif/example/test:second",
                    "//motif/example/test:second",
                    second_module,
                ),
            ],
        )
        bazel.assert_called_once()
        self.assertEqual(bazel.call_args.kwargs, {"capture_output": True})
        self.assertEqual(
            bazel.call_args.args[0][1],
            'set("//kernel/example:first_benchmark_module" '
            '"//motif/example/test:second")',
        )

    def test_parses_configured_source_closures(self):
        payload = {
            "results": [
                {
                    "target": {
                        "type": "RULE",
                        "rule": {
                            "name": "//kernel/example:first_benchmark_module",
                            "ruleInput": ["//kernel/example:first"],
                        },
                    }
                },
                {
                    "target": {
                        "type": "RULE",
                        "rule": {
                            "name": "//kernel/example:first",
                            "ruleInput": [
                                "//kernel/example:first.loom",
                                "//motif/example:shared",
                            ],
                        },
                    }
                },
                {
                    "target": {
                        "type": "RULE",
                        "rule": {
                            "name": "//motif/example:shared",
                            "ruleInput": ["//motif/example:shared.loom"],
                        },
                    }
                },
                {
                    "target": {
                        "type": "SOURCE_FILE",
                        "sourceFile": {"name": "//kernel/example:first.loom"},
                    }
                },
                {
                    "target": {
                        "type": "SOURCE_FILE",
                        "sourceFile": {"name": "//motif/example:shared.loom"},
                    }
                },
            ]
        }

        closures = benchmark_sweep._parse_source_closures(
            payload, ["//kernel/example:first_benchmark_module"]
        )

        self.assertEqual(
            closures,
            {
                "//kernel/example:first_benchmark_module": (
                    benchmark_sweep.SourceClosure(
                        dependencies=(
                            "//kernel/example:first",
                            "//motif/example:shared",
                        ),
                        sources=(
                            "//kernel/example:first.loom",
                            "//motif/example:shared.loom",
                        ),
                    )
                )
            },
        )

    def test_incremental_workspace_executes_only_changed_archives(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            output_root = repository_root / "evidence" / "sweep"
            runner = repository_root / "bazel-out/bin/iree-benchmark-loom"
            first_module = repository_root / "bazel-out/bin/first.loombc"
            second_module = repository_root / "bazel-out/bin/second.loombc"
            first_source = repository_root / "kernel/example/first.loom"
            second_source = repository_root / "kernel/example/second.loom"
            shared_source = repository_root / "motif/example/shared.loom"
            for path, contents in (
                (runner, b"runner"),
                (first_module, b"first module"),
                (second_module, b"second module"),
                (first_source, b"first source"),
                (second_source, b"second source"),
                (shared_source, b"shared source"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            args = SimpleNamespace(
                configs=["amdgpu"],
                benchmark_args=["--", "--dry-run"],
                device="amdgpu://0",
                output_dir=str(output_root),
                rerun_all=False,
                targets=["//kernel/example:first", "//kernel/example:second"],
            )
            modules = [
                benchmark_sweep.BenchmarkModule(
                    "//kernel/example:first", "//kernel/example:first", first_module
                ),
                benchmark_sweep.BenchmarkModule(
                    "//kernel/example:second", "//kernel/example:second", second_module
                ),
            ]
            closures = {
                "//kernel/example:first": benchmark_sweep.SourceClosure(
                    dependencies=("//motif/example:shared",),
                    sources=(
                        "//kernel/example:first.loom",
                        "//motif/example:shared.loom",
                    ),
                ),
                "//kernel/example:second": benchmark_sweep.SourceClosure(
                    dependencies=(),
                    sources=("//kernel/example:second.loom",),
                ),
            }

            def resolve_selected_modules(
                unused_repository_root, targets, unused_bazel_options, unused_bazel
            ):
                return [
                    module for module in modules if module.selection_label in targets
                ]

            def resolve_selected_closures(targets, unused_bazel_options, unused_bazel):
                return {target: closures[target] for target in targets}

            def write_bundle(command):
                bundle_argument = next(
                    argument
                    for argument in command
                    if argument.startswith("--artifact-bundle-dir=")
                )
                bundle_path = Path(bundle_argument.split("=", 1)[1])
                bundle_path.mkdir(parents=True)
                (bundle_path / "manifest.json").write_text("{}\n", encoding="utf-8")
                benchmark_name = "@" + Path(command[1]).stem
                (bundle_path / "results.json").write_text(
                    json.dumps(
                        {
                            "benchmarks": [{"benchmark": benchmark_name}],
                            "work_items": [{"work_item_index": 0}],
                        }
                    ),
                    encoding="utf-8",
                )

            with (
                mock.patch.object(
                    benchmark_sweep,
                    "_new_run_identity",
                    side_effect=[
                        ("run-1", "2026-08-12T00:00:00Z"),
                        ("run-2", "2026-08-12T00:01:00Z"),
                        ("run-3", "2026-08-12T00:02:00Z"),
                        ("run-4", "2026-08-12T00:03:00Z"),
                        ("run-5", "2026-08-12T00:04:00Z"),
                        ("run-6", "2026-08-12T00:05:00Z"),
                        ("run-7", "2026-08-12T00:06:00Z"),
                        ("run-8", "2026-08-12T00:07:00Z"),
                    ],
                ),
                mock.patch.object(
                    benchmark_sweep,
                    "_host_identity",
                    return_value={"node": "test", "platform": "test"},
                ),
                mock.patch.object(
                    benchmark_sweep,
                    "resolve_modules",
                    side_effect=resolve_selected_modules,
                ),
                mock.patch.object(
                    benchmark_sweep,
                    "resolve_target_patterns",
                    side_effect=lambda unused_root, targets, unused_bazel: sorted(
                        set(targets)
                    ),
                ),
                mock.patch.object(
                    benchmark_sweep,
                    "query_source_closures",
                    side_effect=resolve_selected_closures,
                ),
                mock.patch.object(
                    benchmark_sweep,
                    "resolve_runner",
                    return_value=runner,
                ),
                mock.patch.object(dev, "_bazel") as bazel,
                mock.patch.object(dev, "_run", side_effect=write_bundle) as run,
            ):
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                self.assertEqual(run.call_count, 2)
                first_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(first_manifest["summary"]["executed_module_count"], 2)
                self.assertEqual(first_manifest["modules"][0]["benchmarks"], ["@first"])

                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                run.assert_not_called()
                second_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(second_manifest["summary"]["reused_module_count"], 2)

                shared_source.write_bytes(b"source-only change")
                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                run.assert_not_called()
                source_only_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                source_only_module = next(
                    module
                    for module in source_only_manifest["modules"]
                    if module["label"] == "//kernel/example:first"
                )
                self.assertTrue(source_only_module["potentially_affected"])
                self.assertFalse(source_only_module["artifact_changed"])
                self.assertEqual(source_only_module["state"], "reused")

                args.targets = ["//kernel/example:first"]
                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                run.assert_not_called()
                subset_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(subset_manifest["summary"]["selected_module_count"], 1)
                self.assertEqual(subset_manifest["summary"]["reused_module_count"], 1)

                args.targets = ["//kernel/example:first", "//kernel/example:second"]
                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                run.assert_not_called()
                restored_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(restored_manifest["summary"]["reused_module_count"], 2)

                shared_source.write_bytes(b"shared source changed again")
                first_module.write_bytes(b"first module changed")
                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                self.assertEqual(run.call_count, 1)
                self.assertEqual(Path(run.call_args.args[0][1]), first_module)
                third_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(third_manifest["summary"]["executed_module_count"], 1)
                self.assertEqual(third_manifest["summary"]["reused_module_count"], 1)
                changed_module = next(
                    module
                    for module in third_manifest["modules"]
                    if module["label"] == "//kernel/example:first"
                )
                self.assertEqual(
                    changed_module["changed_sources"],
                    [
                        {
                            "change": "modified",
                            "label": "//motif/example:shared.loom",
                        }
                    ],
                )

                runner.write_bytes(b"changed runner")
                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                self.assertEqual(run.call_count, 2)
                runner_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertTrue(
                    all(
                        "runner_changed" in module["reasons"]
                        for module in runner_manifest["modules"]
                    )
                )

                args.rerun_all = True
                run.reset_mock()
                benchmark_sweep.run(
                    args,
                    repository_root=repository_root,
                    bazel=bazel,
                    run_command=run,
                )
                self.assertEqual(run.call_count, 2)
                forced_manifest = json.loads(
                    (output_root / "latest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(forced_manifest["summary"]["executed_module_count"], 2)
                self.assertTrue(
                    all(
                        "rerun_all" in module["reasons"]
                        for module in forced_manifest["modules"]
                    )
                )

    def test_refuses_existing_nonworkspace_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "existing"
            output_root.mkdir()
            (output_root / "some-result.json").write_text("{}", encoding="utf-8")
            args = SimpleNamespace(
                configs=["amdgpu"],
                benchmark_args=[],
                device="amdgpu://0",
                output_dir=str(output_root),
                rerun_all=False,
                targets=["//kernel/example:first"],
            )

            with (
                mock.patch.object(dev, "_bazel") as bazel,
                self.assertRaisesRegex(
                    benchmark_sweep.Error, "not an initialized sweep workspace"
                ),
            ):
                dev._benchmark(args)

        bazel.assert_not_called()

    def test_refuses_runner_output_redirection(self):
        with self.assertRaisesRegex(benchmark_sweep.Error, "owns the --output flag"):
            benchmark_sweep._validate_runner_args(["--output=elsewhere.json"])

    def test_refuses_noncanonical_resolved_benchmark_target(self):
        with self.assertRaisesRegex(benchmark_sweep.Error, "package-local rule names"):
            benchmark_sweep.validate_targets(["//kernel/example/..."])


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

            with mock.patch.object(dev, "REPOSITORY_ROOT", repository_root):
                sources = dev._loom_sources([])

        self.assertEqual(
            sources,
            sorted([experimental_source.resolve(), kernel_source.resolve()]),
        )

    def test_runs_one_batch_formatter_command(self):
        sources = [Path("/repo/motif/a.loom"), Path("/repo/motif/b.loom")]

        with mock.patch.object(dev, "_bazel") as bazel:
            dev._run_loom_format("--check", sources)

        bazel.assert_called_once_with(
            [
                "run",
                dev.LOOM_FORMAT_TARGET,
                "--",
                "--check",
                "/repo/motif/a.loom",
                "/repo/motif/b.loom",
            ]
        )

    def test_format_defaults_to_fix_mode(self):
        parser = dev._create_parser()

        self.assertEqual(parser.parse_args(["format"]).mode, "fix")
        self.assertEqual(parser.parse_args(["format", "--fix"]).mode, "fix")
        self.assertEqual(parser.parse_args(["format", "--check"]).mode, "check")

    def test_check_mode_is_read_only(self):
        source = Path("/repo/motif/a.loom")
        args = SimpleNamespace(mode="check", sources=[str(source)], staged=False)

        with (
            mock.patch.object(dev, "_loom_sources", return_value=[source]),
            mock.patch.object(dev, "_run_loom_format") as run_formatter,
            mock.patch.object(dev, "_stage_loom_sources") as stage_sources,
        ):
            dev._format(args)

        run_formatter.assert_called_once_with("--check", [source])
        stage_sources.assert_not_called()


class StagedLoomFormatTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name)
        self._git("init", "--quiet")
        self._git("config", "user.email", "loom-format-test@example.com")
        self._git("config", "user.name", "Loom Format Test")
        hooks_path = self.repository_root / ".git" / "test-hooks"
        hooks_path.mkdir()
        self._git("config", "core.hooksPath", str(hooks_path))
        repository_root_patch = mock.patch.object(
            dev, "REPOSITORY_ROOT", self.repository_root
        )
        repository_root_patch.start()
        self.addCleanup(repository_root_patch.stop)

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
            dev._staged_loom_sources(),
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
            mock.patch.object(dev, "_run_loom_format") as run_formatter,
            self.assertRaisesRegex(dev.UserError, "partially staged Loom sources"),
        ):
            dev._format(self._format_args())

        run_formatter.assert_not_called()
        self.assertEqual(source.read_text(encoding="utf-8"), "unstaged\n")
        self.assertEqual(self._git("show", ":motif/example.loom"), "staged\n")

    def test_fix_formats_restages_and_checks_exact_staged_sources(self):
        source = self._write("motif/example.loom", "base\n")
        self._commit_all()
        source.write_text("not canonical\n", encoding="utf-8")
        self._git("add", "motif/example.loom")
        formatter_calls = []

        def run_formatter(mode, sources):
            formatter_calls.append((mode, tuple(sources)))
            if mode == "--in-place":
                sources[0].write_text("canonical\n", encoding="utf-8")
            else:
                self.assertEqual(sources[0].read_text(encoding="utf-8"), "canonical\n")

        with mock.patch.object(dev, "_run_loom_format", side_effect=run_formatter):
            dev._format(self._format_args())

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

        with mock.patch.object(dev, "_run_loom_format") as run_formatter:
            dev._format(self._format_args())

        run_formatter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
