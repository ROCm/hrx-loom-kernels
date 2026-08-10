# GGML Tensor Format Motifs

This package family implements GGML in-memory tensor encodings as reusable Loom
functions and templates. GGUF is the container and metadata format: it carries
persistent tensor encodings such as Q4_K and Q6_K. GGML backends also introduce
transient compute formats such as Vulkan's Q8_1 x4 activation packing. Both are
representation motifs; neither inherits a GGML launch ABI. Host loaders and
command programs own GGUF parsing, and these motifs begin at the resulting
buffer and preserved representation facts.

## Implemented formats

| Format | Source | Components | Qualification |
| --- | --- | --- | --- |
| Q8_1 x4 | [`q8_1_x4.loom`](q8_1_x4.loom) | Logical-block and packed-word loads; one-group F32 quantization template | Canonical format and link checks; numerical coverage through the GGML quantization kernel |

One Q8_1 x4 physical group contains four logical 32-element Q8_1 blocks. Its
144-byte layout contains four `(scale, quantized_sum * scale)` F16 pairs followed
by 32 packed I32 words holding 128 signed I8 values. The group template maps one
32-lane wave to the complete group and publishes the packed values and metadata
after workgroup-wide reductions.

The current consumer is the
[GGML-compatible F32 quantization kernel](../../../kernel/ggml/quantize/). Native
GEMM, attention, or fused model kernels link this motif directly rather than
depending on that compatibility wrapper.

## References

The implementation reference snapshot is llama.cpp commit
[`030ebb558a5820b444a8f836ed5cdd46c9b4bd7a`](https://github.com/ggml-org/llama.cpp/commit/030ebb558a5820b444a8f836ed5cdd46c9b4bd7a).

- [GGML Vulkan Q8_1 x4 type definitions](https://github.com/ggml-org/llama.cpp/blob/030ebb558a5820b444a8f836ed5cdd46c9b4bd7a/ggml/src/ggml-vulkan/vulkan-shaders/types.glsl)
- [GGML Vulkan Q8_1 quantization reference](https://github.com/ggml-org/llama.cpp/blob/030ebb558a5820b444a8f836ed5cdd46c9b4bd7a/ggml/src/ggml-vulkan/vulkan-shaders/quantize_q8_1.comp)
- [GGUF format specification](https://github.com/ggml-org/ggml/blob/30bf8685ed4eb0a47f2b06229543327749904150/docs/gguf.md)
