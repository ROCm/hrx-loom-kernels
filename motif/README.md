# Motifs

Motifs are reusable Loom functions and templates. They capture representation,
algorithm, scheduling, or target knowledge without choosing an externally
launchable kernel ABI. Kernel, model, and command-program packages link motifs
and specialize them with the value facts available at their composition roots.

## Map

| Path | Contents |
| --- | --- |
| [`format/`](format/) | Physical tensor representations and their reusable operations. |
| `quantize/` | Representation-independent quantization algorithms once concrete formats establish a shared contract. |
| `attention/` | Reusable attention algorithms and schedules. |
| `target/` | Target-specific realizations of otherwise shared motifs. |

Only namespaces with an implemented owner are created. Their scoped READMEs
become the durable home for specifications, source provenance, design
constraints, and implementation status.

## Dependency contract

A motif package contains `func.def` and `func.template` building blocks and is
declared with `loom_motif_library`. Motifs may depend on other motifs and on
external Loom libraries. They never depend on `kernel/` or declare
`kernel.def`; launch configuration and public execution ABI belong to kernel or
composition roots.

Format-specific and target-specific motifs remain motifs. Reuse is the layer
boundary; universality is not. Shared abstractions are extracted only after
multiple concrete implementations establish the same invariant.
