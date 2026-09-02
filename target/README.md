# Qualification Targets

Target packages define repository-owned execution profiles and may add
build-time profiles only when this library has target policy that HRX's public
profiles cannot express. Kernels otherwise consume HRX target profiles
directly. A target profile provides the typed facts used to prove that a linked
kernel reaches a loader-ready artifact and structured compile report; an
execution profile names the requirements and runner arguments for correctness
execution.

These declarations are qualification policy, not target implementation. The
compiler owns architecture lowering and emission; kernel packages merely name
the profiles they promise to support.

## Map

| Path | Contents |
| --- | --- |
| [`amdgpu/`](amdgpu/) | AMDGPU hardware execution policy. |
