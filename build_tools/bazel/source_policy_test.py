# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
