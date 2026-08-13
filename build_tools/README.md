# Build Tools

Build tooling keeps library declarations compact while applying repository-wide
quality and architecture policy. The root `dev.py` entry point owns setup,
formatting, linting, testing, complete builds, and local benchmark sweeps; this
directory owns the implementation used by those workflows.

## Map

| Path | Contents |
| --- | --- |
| [`bazel/`](bazel/) | Standard-library rule wrappers and source/repository policy checks. |
| [`ci/`](ci/) | Candidate-tree admission checks owned by hosted CI. |

The Loom compiler and generic library rules remain in HRX. Tooling here exists
only for contracts specific to this standard-library repository, so external
projects can consume Loom without inheriting its taxonomy.
