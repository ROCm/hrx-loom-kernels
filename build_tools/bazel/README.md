# Bazel Repository Policy

[`defs.bzl`](defs.bzl) wraps Loom's public Bazel rules with the layer contracts
owned by this repository:

- `loom_motif_library` is confined to `motif/`, rejects kernel dependencies,
  emits a linkable bytecode module, marks source-bearing motifs for qualification
  closure, and adds source-policy coverage.
- `loom_kernel_library` is confined to `kernel/`, keeps GGML compatibility
  entry points as leaf launch surfaces, confines dependencies to motifs,
  requires at least one typed target profile and execution profile, and adds
  the normal format, planning, final-product compilation, and execution checks.
- `loom_test_library` is confined to explicit `test/` packages, keeps its
  wrapper module private and test-only, and passes authored test programs
  directly to correctness runners with production modules as libraries. Test
  leaves require execution qualification but do not manufacture standalone
  target products.
- Execution-profile and Loom toolchain declarations are re-exported unchanged.

Each kernel and test library emits a private runner module carrying the stable
`loom-benchmark-module` tag. HRX verifies direct dependencies while producing
the relocatable library modules; the repository rule then merges that verified
closure solely for `iree-benchmark-loom`, whose current command line accepts
one module and no separate libraries. The merged product is not a published
library or a re-export boundary. Generated format, plan, compile, and execution
tests do not carry benchmark-discovery metadata.

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
