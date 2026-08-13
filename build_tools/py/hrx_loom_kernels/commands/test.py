# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Runs repository policy, format, planning, and compilation tests."""

from __future__ import annotations

import argparse
from typing import Any

from .. import qualification
from ..context import RepositoryContext


def _bazel_arguments(arguments: list[str]) -> list[str]:
    return arguments[1:] if arguments and arguments[0] == "--" else arguments


def _run(args: argparse.Namespace, context: RepositoryContext) -> None:
    qualification.lint_repository(context)
    targets = args.targets or ["//..."]
    context.bazel(["test", *_bazel_arguments(args.bazel_args), *targets])


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "test",
        help="Run format, planning, and compilation policy checks.",
        description=__doc__,
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        dest="targets",
        metavar="PATTERN",
        help=(
            "Test one Bazel target pattern instead of the complete repository "
            "(repeatable)."
        ),
    )
    parser.add_argument("bazel_args", nargs=argparse.REMAINDER)
    parser.set_defaults(handler=_run)
