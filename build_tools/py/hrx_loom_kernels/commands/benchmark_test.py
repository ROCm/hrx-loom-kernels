# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from build_tools.py.hrx_loom_kernels.commands import benchmark


class BenchmarkCommandTest(unittest.TestCase):
    def test_parser_keeps_build_and_runner_configuration_separate(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        benchmark.register(subparsers)

        args = parser.parse_args(
            [
                "benchmark",
                "--config=amdgpu",
                "--device=amdgpu://0",
                "--output-dir=.reports/benchmark/gfx1100",
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

        with mock.patch.object(benchmark, "discover_targets", return_value=catalog):
            targets = benchmark.resolve_target_patterns(
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
                benchmark,
                "discover_targets",
                return_value=["//kernel/gemm:matmul_benchmark_module"],
            ),
            self.assertRaisesRegex(
                benchmark.Error,
                "No benchmark modules match Bazel target patterns",
            ),
        ):
            benchmark.resolve_target_patterns(
                Path("/repo"),
                ["//kernel/attention/..."],
                lambda arguments, capture_output=False: "//kernel/attention:flash\n",
            )

    def test_rejects_empty_target_pattern(self):
        with (
            mock.patch.object(benchmark, "discover_targets") as discover,
            self.assertRaisesRegex(benchmark.Error, "must not be empty"),
        ):
            benchmark.resolve_target_patterns(Path("/repo"), [""], mock.Mock())

        discover.assert_not_called()

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

            bazel = mock.Mock(return_value=cquery_output)
            modules = benchmark.resolve_modules(
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
                benchmark.BenchmarkModule(
                    "//kernel/example:first_benchmark_module",
                    "//kernel/example:first",
                    first_module,
                ),
                benchmark.BenchmarkModule(
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

        closures = benchmark._parse_source_closures(
            payload, ["//kernel/example:first_benchmark_module"]
        )

        self.assertEqual(
            closures,
            {
                "//kernel/example:first_benchmark_module": (
                    benchmark.SourceClosure(
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
                benchmark.BenchmarkModule(
                    "//kernel/example:first", "//kernel/example:first", first_module
                ),
                benchmark.BenchmarkModule(
                    "//kernel/example:second", "//kernel/example:second", second_module
                ),
            ]
            closures = {
                "//kernel/example:first": benchmark.SourceClosure(
                    dependencies=("//motif/example:shared",),
                    sources=(
                        "//kernel/example:first.loom",
                        "//motif/example:shared.loom",
                    ),
                ),
                "//kernel/example:second": benchmark.SourceClosure(
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

            bazel = mock.Mock()
            run = mock.Mock(side_effect=write_bundle)
            with (
                mock.patch.object(
                    benchmark,
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
                    benchmark,
                    "_host_identity",
                    return_value={"node": "test", "platform": "test"},
                ),
                mock.patch.object(
                    benchmark,
                    "resolve_modules",
                    side_effect=resolve_selected_modules,
                ),
                mock.patch.object(
                    benchmark,
                    "resolve_target_patterns",
                    side_effect=lambda unused_root, targets, unused_bazel: sorted(
                        set(targets)
                    ),
                ),
                mock.patch.object(
                    benchmark,
                    "query_source_closures",
                    side_effect=resolve_selected_closures,
                ),
                mock.patch.object(
                    benchmark,
                    "resolve_runner",
                    return_value=runner,
                ),
            ):
                benchmark.run(
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
                benchmark.run(
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
                benchmark.run(
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
                benchmark.run(
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
                benchmark.run(
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
                benchmark.run(
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
                benchmark.run(
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
                benchmark.run(
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

            bazel = mock.Mock()
            with self.assertRaisesRegex(
                benchmark.Error, "not an initialized sweep workspace"
            ):
                benchmark.run(
                    args,
                    repository_root=Path("/repository"),
                    bazel=bazel,
                    run_command=mock.Mock(),
                )

        bazel.assert_not_called()

    def test_refuses_runner_output_redirection(self):
        with self.assertRaisesRegex(benchmark.Error, "owns the --output flag"):
            benchmark._validate_runner_args(["--output=elsewhere.json"])

    def test_refuses_noncanonical_resolved_benchmark_target(self):
        with self.assertRaisesRegex(benchmark.Error, "package-local rule names"):
            benchmark.validate_targets(["//kernel/example/..."])


if __name__ == "__main__":
    unittest.main()
