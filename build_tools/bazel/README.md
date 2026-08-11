# Bazel Repository Policy

[`defs.bzl`](defs.bzl) wraps Loom's public Bazel rules with the layer contracts
owned by this repository:

- `loom_motif_library` is confined to `motif/`, rejects kernel dependencies,
  emits a linked bytecode archive, and adds source-policy coverage.
- `loom_kernel_library` is confined to `kernel/`, keeps GGML compatibility
  packages independent from other kernels, prevents native kernels from using
  compatibility wrappers as foundations, and adds the normal format, planning,
  and compilation checks.
- `loom_test_library` is confined to explicit `test/` packages, keeps its
  wrapper archive private and test-only, and passes authored test programs
  directly to correctness runners with production archives as libraries.
- Generic compile targets and toolchain declarations are re-exported unchanged.

[`source_policy.py`](source_policy.py) verifies the filesystem-level portion of
the contract. Every motif and kernel package has an adjacent `BUILD.bazel`,
explicitly declares each `.loom` source, loads the repository wrappers, and has
a scoped README chain up to its layer root. Motif sources contain no
`kernel.def`; kernel packages contain at least one. Sources below an explicit
`test/` component use `loom_test_library` and may declare private wrapper
kernels without weakening the production motif contract.

`python dev.py lint` runs the complete repository check. `python dev.py build`
and `python dev.py test` run it before invoking Bazel, while generated Bazel
source-policy tests preserve the source-layer checks for direct Bazel users.
