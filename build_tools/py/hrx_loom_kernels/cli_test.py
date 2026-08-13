# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import unittest

from build_tools.py.hrx_loom_kernels import cli


class CommandDiscoveryTest(unittest.TestCase):
    def test_discovers_command_modules_without_a_registration_list(self):
        module_names = {
            module.__name__.rsplit(".", 1)[-1] for module in cli._command_modules()
        }

        self.assertTrue(
            {
                "benchmark",
                "build",
                "compile_report",
                "format",
                "hook",
                "lint",
                "setup",
                "test",
            }.issubset(module_names)
        )

    def test_combined_parser_exposes_every_command(self):
        parser = cli.create_parser()

        self.assertEqual(parser.parse_args(["lint"]).command, "lint")
        self.assertEqual(parser.parse_args(["format", "--check"]).mode, "check")


if __name__ == "__main__":
    unittest.main()
