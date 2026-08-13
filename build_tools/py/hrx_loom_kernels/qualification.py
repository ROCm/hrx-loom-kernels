# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Repository-wide source and benchmark qualification policy."""

from __future__ import annotations

import sys
from pathlib import Path

from .context import RepositoryContext, UserError


LOOM_BENCHMARK_ROOTS = ("experimental", "kernel", "model", "motif")
LOOM_BENCHMARK_TAG = "loom-benchmark-module"
LOOM_MOTIF_ADMISSION_TAG = "loom-admission-motif"


def benchmark_query_expression(repository_root: Path) -> str:
    """Returns the populated repository scope containing benchmark modules."""
    scopes = [
        f"//{root}/..."
        for root in LOOM_BENCHMARK_ROOTS
        if any((repository_root / root).rglob("BUILD.bazel"))
    ]
    if not scopes:
        raise UserError("No benchmark library packages exist in this checkout")
    scope_expression = " union ".join(scopes)
    return f'attr(tags, "{LOOM_BENCHMARK_TAG}", {scope_expression})'


def _motif_admission_query_expression(repository_root: Path) -> str:
    benchmark_targets = benchmark_query_expression(repository_root)
    source_motifs = (
        f'labels(srcs, attr(tags, "{LOOM_MOTIF_ADMISSION_TAG}", //motif/...))'
    )
    return f"{source_motifs} except deps({benchmark_targets})"


def _check_motif_qualification(context: RepositoryContext) -> None:
    output = context.bazel(
        [
            "query",
            _motif_admission_query_expression(context.repository_root),
            "--output=label",
        ],
        capture_output=True,
    )
    unqualified_motifs = sorted(
        line.strip() for line in output.splitlines() if line.strip()
    )
    if unqualified_motifs:
        labels = "\n  ".join(unqualified_motifs)
        raise UserError(
            "Source-bearing motifs require a benchmarkable kernel or test "
            f"qualification leaf:\n  {labels}"
        )


def lint_repository(context: RepositoryContext) -> None:
    """Runs all repository-owned source and dependency policy checks."""
    source_policy_tool = (
        context.repository_root / "build_tools" / "bazel" / "source_policy.py"
    )
    context.run(
        [
            sys.executable,
            "-B",
            str(source_policy_tool),
            f"--repository-root={context.repository_root}",
        ]
    )
    _check_motif_qualification(context)
