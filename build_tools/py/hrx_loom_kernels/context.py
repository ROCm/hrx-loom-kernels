# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Shared repository and process context for developer commands."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class UserError(Exception):
    """Reports a command-line or local-environment problem."""


def format_command(command: list[str]) -> str:
    """Formats an argument vector for the host's native command-line syntax."""
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


@dataclass(frozen=True)
class RepositoryContext:
    """The repository boundary shared by independently owned commands."""

    repository_root: Path

    def bazel_executable(self) -> str:
        executable = shutil.which("bazelisk") or shutil.which("bazel")
        if executable is None:
            raise UserError("Bazelisk or Bazel must be available on PATH")
        return executable

    def run(self, command: list[str], *, capture_output: bool = False) -> str:
        print("+ " + format_command(command), flush=True)
        result = subprocess.run(
            command,
            capture_output=capture_output,
            check=False,
            cwd=self.repository_root,
            text=True,
        )
        if result.returncode != 0:
            if capture_output:
                sys.stdout.write(result.stdout or "")
                sys.stderr.write(result.stderr or "")
            raise subprocess.CalledProcessError(result.returncode, command)
        return (result.stdout or "") if capture_output else ""

    def bazel(self, arguments: list[str], *, capture_output: bool = False) -> str:
        return self.run(
            [self.bazel_executable(), *arguments],
            capture_output=capture_output,
        )
