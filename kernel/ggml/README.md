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

The `.loom` source beside each implementation owns its exact upstream reference
snapshot, supported shapes and types, launch ABI, correctness cases, and
benchmark declarations. Package documentation records the shared architectural
contract rather than duplicating an inventory that the directory already
provides.

Dependencies point inward: GGML kernels may compose native motifs, while motifs
never depend on this compatibility layer. A kernel that becomes useful outside
its GGML operation contract is factored at that boundary instead of turning the
compatibility catalog into a second standard library.
