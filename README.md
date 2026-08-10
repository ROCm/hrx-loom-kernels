# HRX Loom Kernels

This repository is the composable standard library for the
[Loom](https://github.com/ROCm/hrx-system/tree/main/loom) specializing compiler.
It packages reusable motifs, launchable kernels, model and command-program
components, target qualification profiles, correctness cases, and benchmark
workloads without coupling them to one runner or deployment architecture.

Motifs are linkable `func.def` and `func.template` components that express
reusable representations and algorithms without imposing a launch ABI. Kernels
are concrete `kernel.def` entry points that compose motifs for a particular
operation contract and ABI. For example, the
[GGML Q8_1 x4 representation](motif/format/ggml/) is independent of the
[GGML-compatible quantization entry point](kernel/ggml/quantize/) that uses it.

## Quickstart

Select the Loom revision pinned by this repository and build an optimized
portable bytecode archive:

```shell
python dev.py setup --release
bazelisk build -c opt //kernel/ggml/quantize:q8_1_x4_f32
LOOM_MODULE="$PWD/bazel-bin/kernel/ggml/quantize/q8_1_x4_f32.loombc"
```

Run every public correctness case on an AMD GPU:

```shell
bazelisk run -c opt --config=amdgpu \
  @iree//loom/src/loom/tools/iree-test-loom -- \
  "$LOOM_MODULE" \
  --device=amdgpu://0 \
  --sanitizer=access
```

When ROCr is not available through the dynamic loader path, set
`IREE_HAL_AMDGPU_LIBHSA_PATH` to the absolute path of
`libhsa-runtime64.so.1` before running the tool.

Benchmark a declared workload from the same archive:

```shell
bazelisk run -c opt --config=amdgpu \
  @iree//loom/src/loom/tools/iree-benchmark-loom -- \
  "$LOOM_MODULE" \
  --device=amdgpu://0 \
  --benchmark=@ggml_quantize_q8_1_x4_f32_decode \
  --measure=dispatch_complete \
  --batch-size=64 \
  --profile-final-batch=true \
  --output=/tmp/q8_1_x4_f32_decode.json

jq '{
  summary,
  benchmarks,
  work_items: [
    .work_items[] |
    {benchmark, state, correctness, measurement, profiled_dispatch_timing}
  ]
}' /tmp/q8_1_x4_f32_decode.json
```

`dispatch_complete` measures execution through device completion. The benchmark
result also contains the final profiled device distribution and the structured
compile report for the specialized executable.

### Inspect compiler evidence

Cross-compile the archive and retain a detailed report as an optimization
baseline:

```shell
LOOM_COMPILE_TOOL="@iree//loom/src/loom/tools/loom-compile"
LOOM_REPORT_TOOL="@iree//loom/py/loom/tools:loom-compile-report"

bazelisk run -c opt "$LOOM_COMPILE_TOOL" -- \
  "$LOOM_MODULE" \
  --backend=amdgpu-hal \
  --target=gfx11-generic \
  --compile-report=details \
  --compile-report-output=/tmp/q8_1_x4_f32.baseline.json \
  --output=/tmp/q8_1_x4_f32.baseline.hsaco

bazelisk run "$LOOM_REPORT_TOOL" -- \
  show /tmp/q8_1_x4_f32.baseline.json
bazelisk run "$LOOM_REPORT_TOOL" -- \
  suggest /tmp/q8_1_x4_f32.baseline.json --format=json

# After an edit, repeat the compile with candidate output filenames.
bazelisk run "$LOOM_REPORT_TOOL" -- \
  diff /tmp/q8_1_x4_f32.baseline.json \
       /tmp/q8_1_x4_f32.candidate.json --format=json
```

Each key under `findings[].evidence` maps back into the full report; prefix it
with `.` to use it as a `jq` expression. Format and rebuild the `.loombc` before
emitting the candidate. Report diffs are intentionally strict about schema,
target, configuration, workload, and entry identity so deltas remain
attributable to the kernel or compiler change under investigation.

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
python dev.py setup --loom-source /path/to/hrx-system
python dev.py test
```

`python dev.py format` rewrites every `.loom` source through the selected
compiler's canonical formatter. Passing paths formats only those files.

`loom_motif_library` emits a linkable `.loombc` archive and validates that its
sources remain non-launchable. `loom_kernel_library` adds launchable kernels,
benchmark planning, target compilation, and structured compile reports. The
bytecode archive remains the portable deployment unit used for JIT
specialization.
