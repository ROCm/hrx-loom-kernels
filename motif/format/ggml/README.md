# GGML Tensor Format Motifs

This package family implements GGML in-memory tensor encodings as reusable Loom
functions and templates. GGUF is the container and metadata format: it carries
persistent tensor encodings such as Q4_K and Q6_K. GGML backends also introduce
transient compute formats such as Vulkan's Q8_1 x4 activation packing. Both are
representation motifs; neither inherits a GGML launch ABI. Host loaders and
command programs own GGUF parsing, and these motifs begin at the resulting
buffer and preserved representation facts.

Format sources own their physical byte layout, element interpretation, block
addressing, and links to the specification or implementation snapshot from
which those contracts were derived. Reusable accessors are `func.def`
operations; reusable encoders and decoders that require specialization are
`func.template` operations. Neither form chooses grid dimensions or exposes a
kernel launch ABI.

GGML-compatible kernels, native GEMM and attention kernels, and model-level
fusions all link these motifs directly. This keeps formats such as Q4_K, Q6_K,
or transient backend packings from being trapped inside one operator catalog.

## References

- [GGUF format specification](https://github.com/ggml-org/ggml/blob/30bf8685ed4eb0a47f2b06229543327749904150/docs/gguf.md)
