# HRX Loom Kernels

This repository is the composable standard library for the
[Loom](https://github.com/ROCm/hrx-system/tree/main/loom) specializing compiler.
It packages reusable functions and templates, launchable kernels, target
qualification profiles, correctness cases, and benchmark workloads without
coupling them to one model runner or deployment architecture.

The first vertical slice is GGML Q8_1 x4 quantization. It deliberately separates
the physical-layout accessors and quantization template in
`kernel/ggml/quantize/q8.loom` from the launchable F32 packer and its executable
checks in `kernel/ggml/quantize/q8_1_x4_f32.loom`. The kernel derives its exact
launch grid from workload values, so different shapes compose in one command
program without mutating package-global configuration.

## Build and test

The checked-in `MODULE.bazel` selects a specific Loom revision. Bazel expands
one kernel-library declaration into canonical formatting, bytecode linking,
benchmark planning, and target compilation checks:

```shell
python dev.py setup --release
python dev.py test
python dev.py build
```

Compiler co-development keeps the same BUILD labels and policy checks. Selecting
a local HRX worktree makes Bazel rebuild changed compiler tools directly from
uncommitted source:

```shell
python dev.py setup --loom-source ../hrx-system
python dev.py test
```

`python dev.py format` rewrites every `.loom` source through the selected
compiler's canonical formatter. Passing paths formats only those files.

## Repository shape

The path is part of each component's contract:

```text
kernel/
  ggml/quantize/        Standalone GGML quantization kernel packages
motif/
  attention/            Backend-neutral reusable algorithmic components
  target/amdgpu/        AMDGPU implementations of otherwise shared motifs
model/                   Model-specific kernels and subgraph composition roots
target/                  Build-time qualification profiles
```

`loom_library` packages functions and templates as a linkable `.loombc` archive.
`loom_kernel_library` adds launchable kernels and requires benchmark-plan and
target-compilation coverage. Target compilation emits both the loader-ready
artifact and a structured compile report; the bytecode library remains the
portable deployment unit used for JIT specialization.

Motifs take values and facts through ordinary function/template parameters.
Model or command-program roots own configuration that is truly global to one
composition. This keeps a reusable kernel callable many ways inside the same
program and prevents a growing corpus from turning into a matrix of generated
configuration wrappers.
