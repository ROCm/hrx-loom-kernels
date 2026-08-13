# Build Tools

Build tooling keeps library declarations compact while applying repository-wide
quality and architecture policy. The root `dev.py` file is a stable thunk into
the Python package owned here; command implementations and their tests remain
under that package.

## Map

| Path | Contents |
| --- | --- |
| [`bazel/`](bazel/) | Standard-library rule wrappers and source/repository policy checks. |
| [`ci/`](ci/) | Candidate-tree admission checks owned by hosted CI. |
| [`py/`](py/) | Auto-discovered developer commands, shared repository context, and paired tests. |

The Loom compiler and generic library rules remain in HRX. Tooling here exists
only for contracts specific to this standard-library repository, so external
projects can consume Loom without inheriting its taxonomy.
