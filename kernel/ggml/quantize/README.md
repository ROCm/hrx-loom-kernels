# GGML Quantization Kernels

This package contains launchable quantization entry points matching GGML
representations and operation contracts. The physical layouts and reusable
encode/decode bodies live in [`motif/format/ggml/`](../../../motif/format/ggml/).

## Implementation status

| Kernel | Conversion | Shape contract | Qualification |
| --- | --- | --- | --- |
| `@ggml_quantize_q8_1_x4_f32` | Contiguous F32 to GGML Q8_1 x4 | 1-2048 rows; row width 128-32768 and divisible by 128 | Canonical, linked, benchmark-planned, gfx11-generic compiled, and AMDGPU numerically executed |

[`q8_1_x4_f32.loom`](q8_1_x4_f32.loom) derives the exact physical-group count
from `token_count * input_size`. It has no package-global capacity binding, so a
single command program can launch multiple shapes without relinking or mutating
global configuration.

The launch ABI is:

```text
configuration: (token_count: index, input_size: index)
arguments:     (token_count: index, input_size: index,
                input: buffer, output: buffer)
```

Correctness coverage checks positive and negative nonzero groups, packed words,
F16 scales, and scaled quantized sums. The sampled workload covers token counts
1, 4, 8, 17, 32, 63, 128, 129, and 512 at width 2048. Declared benchmark views
cover decode, small batch, and prefill shapes. AMDGPU execution of both cases
currently covers ten generated samples with no failures, skips, or planning
issues.

## References

- [Reusable Q8_1 x4 format motif](../../../motif/format/ggml/)
- [GGML Vulkan Q8_1 quantization reference](https://github.com/ggml-org/llama.cpp/blob/030ebb558a5820b444a8f836ed5cdd46c9b4bd7a/ggml/src/ggml-vulkan/vulkan-shaders/quantize_q8_1.comp)
- [GGML Vulkan Q8_1 x4 layout](https://github.com/ggml-org/llama.cpp/blob/030ebb558a5820b444a8f836ed5cdd46c9b4bd7a/ggml/src/ggml-vulkan/vulkan-shaders/types.glsl)
