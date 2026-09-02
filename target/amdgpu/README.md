# AMDGPU Qualification Targets

`AMDGPU_HARDWARE` in [`profile.bzl`](profile.bzl) runs authored correctness
programs through the AMDGPU HAL backend. It carries Loom target and emitter
requirements, the IREE AMDGPU driver requirement, the AMD GPU runtime resource,
and the shared `loom-amdgpu-tests` resource group used to serialize physical
device access.

Kernel declarations consume HRX's built-in
`@hrx//loom/target/amdgpu:gfx11-generic` profile directly. HRX therefore owns
the compiler selector, descriptor compatibility, and artifact policy for the
final product; this package owns only the execution environment needed to run
its correctness cases.

The generic target profile proves target-family compilation without baking a
specific device choice into reusable motif or kernel sources. Device execution
evidence belongs to the consuming kernel package because correctness
requirements and runtime capabilities are kernel-specific.
