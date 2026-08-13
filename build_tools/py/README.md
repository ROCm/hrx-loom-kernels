# Python Developer Tools

This package owns the repository-local Python tooling reached through the root
`dev.py` thunk. Tool implementations, command-line parsing, tests, and Bazel
test artifacts stay below this package rather than turning the repository root
into an application package.

`hrx_loom_kernels/cli.py` discovers modules in
`hrx_loom_kernels/commands/` and asks each module to register its command. A
command owns its complete parser and handler, so adding or changing a tool does
not require editing the dispatcher or root thunk. Files ending in `_test.py`
are paired command tests and are excluded from command discovery.

Shared process execution, repository location, and Bazel selection live in
`context.py`. Repository-wide source and qualification invariants live in
`qualification.py`. Command-specific behavior remains in its command module;
this boundary keeps `build_tools/py/` from becoming an undifferentiated helper
directory as the toolset grows.

All commands use argument vectors and Python filesystem APIs rather than shell
evaluation. The tools require Python, Git, and Bazel but do not require a POSIX
shell or Unix archive utilities.
