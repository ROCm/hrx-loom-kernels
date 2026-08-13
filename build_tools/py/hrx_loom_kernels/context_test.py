# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import shlex
import subprocess
import unittest
from unittest import mock

from build_tools.py.hrx_loom_kernels import context


class CommandFormattingTest(unittest.TestCase):
    def test_uses_windows_argument_rendering_on_windows(self):
        command = ["C:/Program Files/Bazel/bazel.exe", "build", "//kernel/..."]

        with mock.patch.object(context.os, "name", "nt"):
            rendered = context.format_command(command)

        self.assertEqual(rendered, subprocess.list2cmdline(command))

    def test_uses_shell_argument_rendering_on_posix(self):
        command = ["bazel", "build", "//kernel/..."]

        with mock.patch.object(context.os, "name", "posix"):
            rendered = context.format_command(command)

        self.assertEqual(rendered, shlex.join(command))


if __name__ == "__main__":
    unittest.main()
