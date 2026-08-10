# Model and Command Programs

Model packages are composition roots. They assemble reusable motifs and
launchable kernels into model-specific subgraphs or complete command programs,
own the configuration that is genuinely global to that composition, and
preserve value facts across orchestration boundaries for specialization.

A model package may launch native kernels and GGML compatibility kernels while
coverage is being migrated. Reusable representation or algorithm code flows
down into `motif/`; reusable launch surfaces flow into domain packages under
`kernel/`. Code remains under `model/<family>/` when its contract is inherently
tied to that model architecture or command schedule.

This layer is also where GGUF metadata becomes buffers, dimensions, encoding
identities, and other compile-time or invocation-time facts. Device motifs
consume those projected facts and never parse the container directly.

## Map

Model-family namespaces are created with their first checked command program or
model-specific kernel. Each namespace README owns supported model variants,
weight and tokenizer assumptions, exported command surfaces, kernel and motif
dependencies, correctness coverage, benchmark scenarios, and deployment
status.
