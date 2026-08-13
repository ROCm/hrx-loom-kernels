# Bazel Repository Policy

[`defs.bzl`](defs.bzl) wraps Loom's public Bazel rules with the layer contracts
owned by this repository:

- `loom_motif_library` is confined to `motif/`, rejects kernel dependencies,
  emits a linked bytecode archive, marks source-bearing motifs for qualification
  closure, and adds source-policy coverage.
- `loom_kernel_library` is confined to `kernel/`, keeps GGML compatibility
  entry points as leaf launch surfaces, confines dependencies to motifs,
  requires compile and execution qualification, and adds the normal format,
  planning, and compilation checks.
- `loom_test_library` is confined to explicit `test/` packages, keeps its
  wrapper archive private and test-only, and passes authored test programs
  directly to correctness runners with production archives as libraries. Test
  leaves also require compile and execution qualification.
- Generic compile targets and toolchain declarations are re-exported unchanged.

Each kernel and test library emits a private filegroup carrying the stable
`loom-benchmark-module` tag. The filegroup forwards only the library's `.loombc`
archive, allowing repository tooling to discover inputs for
`iree-benchmark-loom` without depending on Loom's private rule kinds. Generated
format, plan, compile, and execution tests do not carry benchmark-discovery
metadata.

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
The lint command also queries repository-owned tags and ordinary dependency
edges to reject any source-bearing motif that is not reachable from a
benchmarkable kernel or test leaf; no global target inventory or upstream
private rule kind participates.

The complete admission and review contract is documented in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md).
