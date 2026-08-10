# HRX Loom Kernels

This repository is the composable standard library for the
[Loom](https://github.com/ROCm/hrx-system/tree/main/loom) specializing compiler.
It packages reusable motifs, launchable kernels, model and command-program
components, target qualification profiles, correctness cases, and benchmark
workloads without coupling them to one runner or deployment architecture.

The top-level documentation is a map. Each substantive directory owns a scoped
`README.md` describing its contract, contents, upstream references, and current
implementation status. The repository linter requires that documentation chain
for every motif and kernel package.

## Repository map

| Path | Contents |
| --- | --- |
| [`build_tools/`](build_tools/) | Repository setup, policy, and build integration. |
| [`build_tools/bazel/`](build_tools/bazel/) | Repository policy wrappers around Loom's public Bazel rules. |
| [`motif/`](motif/) | Linkable `func.def` and `func.template` building blocks. |
| [`motif/format/`](motif/format/) | Reusable physical tensor-representation components. |
| [`motif/format/ggml/`](motif/format/ggml/) | GGML tensor encodings independent of GGML's execution ABI. |
| [`kernel/`](kernel/) | Launchable `kernel.def` packages with tests and benchmarks. |
| [`kernel/ggml/`](kernel/ggml/) | Leaf compatibility implementations of GGML operation kernels. |
| [`kernel/ggml/quantize/`](kernel/ggml/quantize/) | GGML-compatible quantization entry points. |
| [`model/`](model/) | Model and command-program composition roots. |
| [`target/`](target/) | Reusable build-time qualification profiles. |
| [`target/amdgpu/`](target/amdgpu/) | AMDGPU artifact qualification profiles. |

The first vertical slice separates the reusable
[GGML Q8_1 x4 representation motif](motif/format/ggml/) from its
[GGML-compatible F32 quantization kernel](kernel/ggml/quantize/). Native Loom
kernels can consume the same representation motif without adopting GGML's
launch ABI.

## Build and test

The checked-in `MODULE.bazel` selects a specific Loom revision. Bazel expands
each library declaration into its formatting, linking, planning, and target
qualification policy:

```shell
python dev.py setup --release
python dev.py lint
python dev.py test
python dev.py build
```

Compiler co-development keeps the same BUILD labels and policy checks. A local
HRX worktree makes Bazel rebuild changed compiler tools directly from
uncommitted source:

```shell
python dev.py setup --loom-source ../hrx-system
python dev.py test
```

`python dev.py format` rewrites every `.loom` source through the selected
compiler's canonical formatter. Passing paths formats only those files.

`loom_motif_library` emits a linkable `.loombc` archive and validates that its
sources remain non-launchable. `loom_kernel_library` adds launchable kernels,
benchmark planning, target compilation, and structured compile reports. The
bytecode archive remains the portable deployment unit used for JIT
specialization.
