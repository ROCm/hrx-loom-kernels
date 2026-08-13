# GGML Quantization Kernels

This package contains launchable quantization entry points matching GGML
representations and operation contracts. The physical layouts and reusable
encode/decode bodies live in [`motif/format/ggml/`](../../../motif/format/ggml/).

Quantization kernels own the externally visible conversion semantics, shape
contract, launch configuration, and buffer ABI. Format motifs own byte layout,
block access, and reusable encode/decode computation. Keeping that boundary
explicit lets compatibility entry points and native fused kernels share the
same representation code without sharing an execution contract.

Each `.loom` source is a self-contained deployment and validation unit: its
header documents the exact upstream reference and ABI, its `check.case`
declarations exercise correctness, and its `check.benchmark` declarations name
representative workloads. BUILD declarations add linkage and target
qualification without moving shape configuration into package-global state.
