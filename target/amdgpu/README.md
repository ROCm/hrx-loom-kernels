# AMDGPU Qualification Targets

| Profile | Backend | Loom target | Artifact | Status |
| --- | --- | --- | --- | --- |
| [`gfx11_generic`](BUILD.bazel) | `amdgpu-hal` | `gfx11-generic` | HSACO | Q8_1 x4 quantization compiles with a structured summary report |

The generic profile proves target-family compilation without baking a specific
device choice into reusable motif or kernel sources. Device execution evidence
belongs to the consuming kernel package because correctness requirements and
runtime capabilities are kernel-specific.
