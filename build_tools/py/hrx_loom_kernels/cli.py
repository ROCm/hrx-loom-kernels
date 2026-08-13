# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Repository maintenance entry point for the Loom kernel library."""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from . import commands
from .context import RepositoryContext, UserError


def _command_modules() -> list[ModuleType]:
    """Imports every command module without a central registration list."""
    modules = []
    for module_info in sorted(
        pkgutil.iter_modules(commands.__path__), key=lambda info: info.name
    ):
        if module_info.name.startswith("_") or module_info.name.endswith("_test"):
            continue
        module = importlib.import_module(f"{commands.__name__}.{module_info.name}")
        if not callable(getattr(module, "register", None)):
            raise UserError(
                "Developer command module must define register(subparsers): "
                f"{module.__name__}"
            )
        modules.append(module)
    if not modules:
        raise UserError("No developer command modules are available")
    return modules


def create_parser() -> argparse.ArgumentParser:
    """Builds the CLI from command-owned parser registrations."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for module in _command_modules():
        module.register(subparsers)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    repository_root: Path | None = None,
) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if repository_root is None:
        repository_root = Path(__file__).resolve().parents[3]
    context = RepositoryContext(repository_root.resolve())
    try:
        args.handler(args, context)
    except UserError as error:
        parser.error(str(error))
    except subprocess.CalledProcessError as error:
        return error.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
