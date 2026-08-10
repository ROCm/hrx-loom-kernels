#!/usr/bin/env python3
# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Checks whether Loom sources match their repository architecture layer."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


KERNEL_DEFINITION_PATTERN = re.compile(r"^\s*kernel\.def(?:\s|@)", re.MULTILINE)


def _code_without_line_comments(source: str) -> str:
    return "\n".join(line.partition("//")[0] for line in source.splitlines())


def check_sources(layer: str, sources: Iterable[tuple[str, str]]) -> list[str]:
    """Returns policy violations for named Loom source strings."""
    source_definitions = [
        (
            name,
            bool(KERNEL_DEFINITION_PATTERN.search(_code_without_line_comments(text))),
        )
        for name, text in sources
    ]
    if layer == "motif":
        return [
            f"{name}: motif sources cannot declare kernel.def"
            for name, has_kernel_definition in source_definitions
            if has_kernel_definition
        ]
    if layer == "kernel" and not any(
        has_kernel_definition for _, has_kernel_definition in source_definitions
    ):
        names = ", ".join(name for name, _ in source_definitions)
        return [f"kernel package has no kernel.def declaration: {names}"]
    return []


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=["kernel", "motif"], required=True)
    parser.add_argument("sources", metavar="SOURCE", nargs="+")
    return parser


def main() -> int:
    args = _create_parser().parse_args()
    source_paths = [Path(path) for path in args.sources]
    violations = check_sources(
        args.layer,
        [(str(path), path.read_text()) for path in source_paths],
    )
    for violation in violations:
        print(violation, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
