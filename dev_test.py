# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import unittest
from pathlib import Path

import dev


class LoomFormatOutputTest(unittest.TestCase):
    def test_selects_executable_from_mixed_query_output(self):
        output = "\n".join(
            [
                "repository fetch notice",
                "bazel-out/k8-fastbuild/bin/loom-format.runfiles_manifest",
                "bazel-out/k8-fastbuild/bin/loom-format/loom-format",
            ]
        )

        self.assertEqual(
            dev._select_loom_format_output(output),
            Path("bazel-out/k8-fastbuild/bin/loom-format/loom-format"),
        )

    def test_accepts_windows_executable(self):
        self.assertEqual(
            dev._select_loom_format_output(
                "bazel-out/x64_windows-fastbuild/bin/loom-format.exe\n"
            ),
            Path("bazel-out/x64_windows-fastbuild/bin/loom-format.exe"),
        )

    def test_rejects_ambiguous_executable_outputs(self):
        with self.assertRaisesRegex(
            dev.UserError, "Expected one loom-format executable output"
        ):
            dev._select_loom_format_output("a/loom-format\nb/loom-format\n")


if __name__ == "__main__":
    unittest.main()
