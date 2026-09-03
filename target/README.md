# Qualification Targets

This layer is reserved for target or execution policy that this library owns
and HRX's public profiles cannot express. A target profile provides the typed
facts used to prove that a linked kernel reaches a loader-ready artifact and
structured compile report; an execution profile names the requirements and
runner arguments for correctness execution.

These declarations are qualification policy, not target implementation. The
compiler owns architecture lowering and emission; kernel packages merely name
the profiles they promise to support.

There is currently no repository-specific target package. Kernels and tests
consume HRX's canonical AMDGPU target and execution profiles directly; adding
a local package requires a concrete policy difference rather than an alias or
copy of upstream state.
