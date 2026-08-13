# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Checks or fixes canonical formatting for selected Loom sources."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
from typing import Any

from ..context import RepositoryContext, UserError


LOOM_FORMAT_TARGET = "@iree//loom/src/loom/tools/loom-format"
LOOM_SOURCE_ROOTS = ("experimental", "kernel", "model", "motif", "target")


def _is_loom_source_path(path: str) -> bool:
    source_path = PurePosixPath(path)
    return (
        "\\" not in path
        and source_path.as_posix() == path
        and not source_path.is_absolute()
        and ".." not in source_path.parts
        and source_path.suffix == ".loom"
        and bool(source_path.parts)
        and source_path.parts[0] in LOOM_SOURCE_ROOTS
    )


def _normalize_loom_source(repository_root: Path, argument: str | Path) -> Path:
    source = Path(argument).expanduser()
    if not source.is_absolute():
        source = repository_root / source
    source = source.resolve()
    try:
        relative_source = source.relative_to(repository_root.resolve()).as_posix()
    except ValueError as error:
        raise UserError(f"Loom source is outside the repository: {source}") from error
    if not _is_loom_source_path(relative_source):
        source_roots = ", ".join(f"{root}/" for root in LOOM_SOURCE_ROOTS)
        raise UserError(
            "Loom sources must be .loom files below one of: "
            f"{source_roots}; got {relative_source}"
        )
    if not source.is_file():
        raise UserError(f"Loom source does not exist: {source}")
    return source


def _loom_sources(repository_root: Path, arguments: list[str]) -> list[Path]:
    if arguments:
        sources = [_normalize_loom_source(repository_root, path) for path in arguments]
    else:
        sources = []
        for relative_root in LOOM_SOURCE_ROOTS:
            source_root = repository_root / relative_root
            if source_root.is_dir():
                sources.extend(
                    _normalize_loom_source(repository_root, path)
                    for path in source_root.rglob("*.loom")
                )
    return sorted(set(sources))


def _source_relative_path(repository_root: Path, source: Path) -> str:
    return source.relative_to(repository_root.resolve()).as_posix()


def _staged_loom_sources(context: RepositoryContext) -> list[Path]:
    output = context.run(
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMR",
            "-z",
            "--",
            *LOOM_SOURCE_ROOTS,
        ],
        capture_output=True,
    )
    sources = []
    for path in output.split("\0"):
        if not path or not _is_loom_source_path(path):
            continue
        source = (context.repository_root / path).resolve()
        if source.is_file():
            sources.append(_normalize_loom_source(context.repository_root, source))
    return sorted(set(sources))


def _unstaged_loom_sources(
    context: RepositoryContext, sources: list[Path]
) -> list[str]:
    if not sources:
        return []
    relative_sources = [
        _source_relative_path(context.repository_root, source) for source in sources
    ]
    output = context.run(
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--no-ext-diff",
            "--name-only",
            "-z",
            "--",
            *relative_sources,
        ],
        capture_output=True,
    )
    return sorted(path for path in output.split("\0") if path)


def _stage_loom_sources(context: RepositoryContext, sources: list[Path]) -> None:
    if not sources:
        return
    context.run(
        [
            "git",
            "--literal-pathspecs",
            "add",
            "--",
            *[
                _source_relative_path(context.repository_root, source)
                for source in sources
            ],
        ]
    )


def _run_loom_format(
    context: RepositoryContext, mode: str, sources: list[Path]
) -> None:
    context.bazel(
        [
            "run",
            LOOM_FORMAT_TARGET,
            "--",
            mode,
            *[str(source) for source in sources],
        ]
    )


def _run(args: argparse.Namespace, context: RepositoryContext) -> None:
    if args.staged and args.sources:
        raise UserError("--staged cannot be combined with explicit source paths")
    sources = (
        _staged_loom_sources(context)
        if args.staged
        else _loom_sources(context.repository_root, args.sources)
    )
    if not sources:
        selection = "staged " if args.staged else ""
        print(f"No {selection}Loom source files to format", flush=True)
        return
    if args.staged and args.mode == "fix":
        unstaged_sources = _unstaged_loom_sources(context, sources)
        if unstaged_sources:
            rendered_sources = ", ".join(unstaged_sources)
            raise UserError(
                "Cannot format partially staged Loom sources without staging "
                f"uncommitted hunks: {rendered_sources}"
            )

    if args.mode == "check":
        _run_loom_format(context, "--check", sources)
        print(f"Checked {len(sources)} Loom source files", flush=True)
        return

    _run_loom_format(context, "--in-place", sources)
    if args.staged:
        _stage_loom_sources(context, sources)
    _run_loom_format(context, "--check", sources)
    print(f"Formatted and checked {len(sources)} Loom source files", flush=True)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "format",
        help="Check or fix canonical formatting for selected Loom sources.",
        description=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_const",
        const="check",
        dest="mode",
        help="Check canonical formatting without changing files.",
    )
    mode.add_argument(
        "--fix",
        action="store_const",
        const="fix",
        dest="mode",
        help="Rewrite noncanonical files and verify the result (default).",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Select staged .loom files and restage canonical rewrites.",
    )
    parser.add_argument("sources", nargs="*")
    parser.set_defaults(handler=_run, mode="fix")
