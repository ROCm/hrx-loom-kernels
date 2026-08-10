# Kernels

Kernel packages provide externally launchable `kernel.def` entry points. They
own execution ABI, workload-derived launch configuration, correctness cases,
benchmark workloads, and target qualification. Reusable representation and
algorithm bodies live under `motif/` and are linked into these launch surfaces.

## Map

| Path | Contents |
| --- | --- |
| [`ggml/`](ggml/) | Compatibility kernels matching GGML operation semantics and execution contracts. |
| `attention/` | Native launchable attention kernels. |
| `gemm/` | Native launchable matrix kernels. |

Native domain namespaces are created with their first implementation rather
than as empty scaffolding.

## Dependency contract

Every package is declared with `loom_kernel_library` and contains at least one
`kernel.def`. GGML compatibility packages are leaf launch surfaces and depend
only on motifs or external libraries, never on other kernel packages. Native
kernel packages likewise consume motifs directly and never use
`kernel/ggml/` as an implementation foundation.

Model and command-program roots may launch either family while a deployment is
being migrated. The dependency boundary prevents compatibility ABI decisions
from leaking into native kernels.
