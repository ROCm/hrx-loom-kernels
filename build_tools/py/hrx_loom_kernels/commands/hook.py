# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Installs the repository's staged-source pre-commit hook."""

from __future__ import annotations

import argparse
import shutil
from typing import Any

from ..context import RepositoryContext, UserError


def _run(args: argparse.Namespace, context: RepositoryContext) -> None:
    del args
    lefthook = shutil.which("lefthook")
    if lefthook is None:
        raise UserError("Lefthook must be available on PATH")
    context.run([lefthook, "install"])


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "hook",
        help="Install the repository's staged-source pre-commit hook.",
        description=__doc__,
    )
    parser.set_defaults(handler=_run)
