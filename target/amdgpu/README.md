# AMDGPU Qualification Targets

`AMDGPU_HARDWARE` in [`profile.bzl`](profile.bzl) runs authored correctness
programs through the AMDGPU HAL backend. It carries Loom target and emitter
requirements, the IREE AMDGPU driver requirement, the AMD GPU runtime resource,
and the shared `loom-amdgpu-tests` resource group used to serialize physical
device access.

| Profile | Backend | Loom target | Artifact | Status |
| --- | --- | --- | --- | --- |
| [`gfx11_generic`](BUILD.bazel) | `amdgpu-hal` | `gfx11-generic` | HSACO | Q8_1 x4 quantization compiles with a structured summary report |

The generic profile proves target-family compilation without baking a specific
device choice into reusable motif or kernel sources. Device execution evidence
belongs to the consuming kernel package because correctness requirements and
runtime capabilities are kernel-specific.
