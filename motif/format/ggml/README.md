# GGML Tensor Format Motifs

This package family implements GGML in-memory tensor encodings as reusable Loom
functions and templates. GGUF is the container and metadata format: it carries
persistent tensor encodings used by model weights. GGML backends also introduce
transient compute encodings for activations. Both are representation motifs;
neither inherits a GGML launch ABI. Host loaders and command programs own GGUF
parsing, and these motifs begin at the resulting buffer and preserved
representation facts.

Format sources own their physical byte layout, element interpretation, block
addressing, and links to the specification or implementation snapshot from
which those contracts were derived. Reusable accessors are `func.def`
operations; reusable encoders and decoders that require specialization are
`func.template` operations. Neither form chooses grid dimensions or exposes a
kernel launch ABI.

GGML-compatible kernels, native GEMM and attention kernels, and model-level
fusions all link these motifs directly. This keeps physical encodings and
transient backend packings from being trapped inside one operator catalog.
Consumers can depend on individual format targets or link
`//motif/format/ggml:ggml` as the complete GGML format archive. The aggregate
has no sources of its own: Bazel links the already-checked component archives
once and leaves target specialization to the consuming kernel or program.

## References

- [GGUF format specification](https://github.com/ggml-org/ggml/blob/30bf8685ed4eb0a47f2b06229543327749904150/docs/gguf.md)
