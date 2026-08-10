# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_tools.bazel import source_policy


class SourcePolicyTest(unittest.TestCase):
    def test_motif_accepts_functions_and_templates(self):
        violations = source_policy.check_sources(
            "motif",
            [
                ("format.loom", "func.def @load() {\n  func.return\n}\n"),
                (
                    "quantize.loom",
                    "func.template<quantize.block> device @body() {\n"
                    "  func.return\n"
                    "}\n",
                ),
            ],
        )
        self.assertEqual(violations, [])

    def test_motif_rejects_kernel_definition(self):
        violations = source_policy.check_sources(
            "motif",
            [("bad.loom", "kernel.def @bad() {\n} launch() {\n  kernel.return\n}\n")],
        )
        self.assertEqual(
            violations,
            ["bad.loom: motif sources cannot declare kernel.def"],
        )

    def test_kernel_requires_definition_across_package_sources(self):
        violations = source_policy.check_sources(
            "kernel",
            [
                ("helpers.loom", "func.def @helper() {\n  func.return\n}\n"),
                ("checks.loom", "check.case public @case {\n  check.return\n}\n"),
            ],
        )
        self.assertEqual(
            violations,
            ["kernel package has no kernel.def declaration: helpers.loom, checks.loom"],
        )

    def test_comments_do_not_satisfy_kernel_requirement(self):
        violations = source_policy.check_sources(
            "kernel",
            [("comment.loom", "// kernel.def @not_code()\nfunc.return\n")],
        )
        self.assertEqual(
            violations,
            ["kernel package has no kernel.def declaration: comment.loom"],
        )

    def test_repository_accepts_policy_wrapped_packages(self):
        with TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            motif_root = repository_root / "motif" / "format" / "weird"
            motif_root.mkdir(parents=True)
            for readme_directory in (
                repository_root / "motif",
                repository_root / "motif" / "format",
                motif_root,
            ):
                (readme_directory / "README.md").write_text("# Scope\n")
            (motif_root / "q3.loom").write_text(
                "func.def @load() {\n  func.return\n}\n"
            )
            (motif_root / "BUILD.bazel").write_text(
                'load("//build_tools/bazel:defs.bzl", "loom_motif_library")\n'
                'loom_motif_library(name = "q3", srcs = ["q3.loom"])\n'
            )
            self.assertEqual(source_policy.check_repository(repository_root), [])

    def test_repository_rejects_policy_bypass_and_implicit_source(self):
        with TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            kernel_root = repository_root / "kernel" / "ggml" / "quantize"
            kernel_root.mkdir(parents=True)
            for readme_directory in (
                repository_root / "kernel",
                repository_root / "kernel" / "ggml",
                kernel_root,
            ):
                (readme_directory / "README.md").write_text("# Scope\n")
            (kernel_root / "q3.loom").write_text(
                "kernel.def @q3() {\n} launch() {\n  kernel.return\n}\n"
            )
            (kernel_root / "BUILD.bazel").write_text(
                'load("@iree//loom/build_tools/bazel:defs.bzl", '
                '"loom_kernel_library")\n'
                'loom_kernel_library(name = "q3", srcs = glob(["*.loom"]))\n'
            )
            self.assertEqual(
                source_policy.check_repository(repository_root),
                [
                    "kernel/ggml/quantize/BUILD.bazel: load repository rules "
                    "from //build_tools/bazel:defs.bzl",
                    "kernel/ggml/quantize/BUILD.bazel: kernel-library packages "
                    "cannot bypass repository policy wrappers",
                    "kernel/ggml/quantize/BUILD.bazel: q3.loom is not explicitly "
                    "declared",
                ],
            )

    def test_repository_requires_scoped_readme_chain(self):
        with TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            motif_root = repository_root / "motif" / "format" / "weird"
            motif_root.mkdir(parents=True)
            (motif_root / "q3.loom").write_text(
                "func.def @load() {\n  func.return\n}\n"
            )
            (motif_root / "BUILD.bazel").write_text(
                'load("//build_tools/bazel:defs.bzl", "loom_motif_library")\n'
                'loom_motif_library(name = "q3", srcs = ["q3.loom"])\n'
            )
            self.assertEqual(
                source_policy.check_repository(repository_root),
                [
                    "motif/format/weird: missing scoped README.md",
                    "motif/format: missing scoped README.md",
                    "motif: missing scoped README.md",
                ],
            )


if __name__ == "__main__":
    unittest.main()
