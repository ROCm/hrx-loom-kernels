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
- `loom_test` is confined to explicit `test/` packages and delegates to HRX's
  root-owned linked test module. Test leaves require one execution profile but
  do not manufacture standalone target products.

Each kernel and test declaration emits a private filegroup carrying the stable
`loom-benchmark-module` tag. It forwards HRX's linked test module, which retains
cases and benchmarks owned by the root while excluding test-only symbols from
dependencies. The marker adds discovery metadata without introducing another
link or re-export boundary. Generated format, plan, compile, and execution tests
do not carry benchmark-discovery metadata.

[`source_policy.py`](source_policy.py) verifies the filesystem-level portion of
the contract. Every motif and kernel package has an adjacent `BUILD.bazel`,
explicitly declares each `.loom` source, loads the repository wrappers, and has
a scoped README chain up to its layer root. Motif sources contain no
`kernel.def`; kernel packages contain at least one. Sources below an explicit
`test/` component use `loom_test` and may declare private wrapper
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
