#!/usr/bin/env python3
# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Stable repository entry point for developer tooling."""

from __future__ import annotations

import sys
from pathlib import Path

from build_tools.py.hrx_loom_kernels import cli


if __name__ == "__main__":
    sys.exit(cli.main(repository_root=Path(__file__).resolve().parent))
