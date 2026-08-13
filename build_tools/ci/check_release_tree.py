#!/usr/bin/env python3
# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Rejects branch-local experimental payload from a release candidate tree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def tracked_experimental_paths(repository_root: Path) -> list[str]:
    """Returns experimental paths tracked by the candidate Git index."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "experimental"],
        capture_output=True,
        check=True,
        cwd=repository_root,
        text=True,
    )
    return sorted(path for path in result.stdout.split("\0") if path)


def main() -> int:
    paths = tracked_experimental_paths(REPOSITORY_ROOT)
    if not paths:
        return 0

    print(
        "Release candidates cannot contain branch-local experimental paths:",
        file=sys.stderr,
    )
    for path in paths:
        print(f"  {path}", file=sys.stderr)
    print(
        "Promote reusable work into motif/, kernel/, model/, or target/ and "
        "remove the experimental payload from the candidate tree.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
