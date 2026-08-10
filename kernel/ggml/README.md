# GGML Compatibility Kernels

This tree is the one-to-one compatibility catalog for GGML operation kernels.
Each package preserves the observable operation semantics, launch ABI,
representation requirements, and edge cases needed to replace or compare the
corresponding GGML implementation.

Compatibility coverage is a migration and measurement surface, not the native
Loom architecture. Shared block formats, algorithms, and schedules are factored
into `motif/`; native kernels consume those motifs without inheriting GGML's ABI.
This makes each compatibility port useful both as immediate coverage and as
pressure to extract reusable standard-library components.

## Map and status

| Family | Scope | Status |
| --- | --- | --- |
| [`quantize/`](quantize/) | GGML-compatible tensor quantization entry points | Q8_1 x4 F32 packing implemented and AMDGPU-qualified |

Every package README owns its exact upstream reference snapshot, supported
shapes and types, ABI, correctness evidence, benchmark coverage, and target
qualification status. This level remains a navigable catalog even when the tree
contains hundreds of compatibility kernels.
