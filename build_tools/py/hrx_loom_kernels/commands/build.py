# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Builds every library and target-qualified artifact."""

from __future__ import annotations

import argparse
from typing import Any

from .. import qualification
from ..context import RepositoryContext


def _bazel_arguments(arguments: list[str]) -> list[str]:
    return arguments[1:] if arguments and arguments[0] == "--" else arguments


def _run(args: argparse.Namespace, context: RepositoryContext) -> None:
    qualification.lint_repository(context)
    context.bazel(["build", *_bazel_arguments(args.bazel_args), "//..."])


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "build",
        help="Build every library and target-qualified artifact.",
        description=__doc__,
    )
    parser.add_argument("bazel_args", nargs=argparse.REMAINDER)
    parser.set_defaults(handler=_run)
