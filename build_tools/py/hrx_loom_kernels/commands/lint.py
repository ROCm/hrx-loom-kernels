# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Checks repository architecture and dependency policy."""

from __future__ import annotations

import argparse
from typing import Any

from .. import qualification
from ..context import RepositoryContext


def _run(args: argparse.Namespace, context: RepositoryContext) -> None:
    del args
    qualification.lint_repository(context)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "lint",
        help="Check repository architecture and dependency policy.",
        description=__doc__,
    )
    parser.set_defaults(handler=_run)
