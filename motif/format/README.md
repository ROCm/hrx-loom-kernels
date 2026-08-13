# Tensor Format Motifs

Format motifs describe physical tensor representations and the operations that
make those representations useful inside kernels. Typical components include
block accessors, vector loads and stores, packing and unpacking fragments,
decode and encode bodies, and representation-aware dot-product fragments.

This layer is independent of file containers and launch ABIs. A loader projects
container metadata into buffers, dimensions, strides, encoding identities, and
other Loom facts. Format motifs consume those values; they do not parse files or
inherit the execution contract of the software that originated the encoding.

## Map

| Path | Contents |
| --- | --- |
| [`ggml/`](ggml/) | Persistent GGUF-carried encodings and transient GGML backend formats. |

Algorithms shared across several unrelated representations belong in a sibling
algorithm motif such as `motif/quantize/`. Representation-specific byte layout
and arithmetic remain here even when several kernels consume them.
