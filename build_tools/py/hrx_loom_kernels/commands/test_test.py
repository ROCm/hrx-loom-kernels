# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest import mock

from build_tools.py.hrx_loom_kernels import qualification
from build_tools.py.hrx_loom_kernels.commands import test as test_command
from build_tools.py.hrx_loom_kernels.context import RepositoryContext


class TestCommandTest(unittest.TestCase):
    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        test_command.register(subparsers)
        return parser

    def test_runs_selected_bazel_target_patterns(self):
        args = self._parser().parse_args(
            [
                "test",
                "--target=//kernel/gemm/...",
                "--target=//motif/format/ggml/test/...",
                "--",
                "--config=amdgpu",
            ]
        )
        context = mock.Mock(spec=RepositoryContext)
        context.repository_root = Path("/repository")

        with mock.patch.object(qualification, "lint_repository") as lint_repository:
            test_command._run(args, context)

        lint_repository.assert_called_once_with(context)
        context.bazel.assert_called_once_with(
            [
                "test",
                "--config=amdgpu",
                "//kernel/gemm/...",
                "//motif/format/ggml/test/...",
            ]
        )

    def test_defaults_to_complete_repository(self):
        args = self._parser().parse_args(["test"])
        context = mock.Mock(spec=RepositoryContext)
        context.repository_root = Path("/repository")

        with mock.patch.object(qualification, "lint_repository"):
            test_command._run(args, context)

        context.bazel.assert_called_once_with(["test", "//..."])


if __name__ == "__main__":
    unittest.main()
