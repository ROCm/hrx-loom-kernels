# Qualification Targets

Target packages name reusable build-time qualification profiles. A profile
selects the Loom compiler backend, target identity, and artifact extension used
to prove that a portable `.loombc` library reaches a loader-ready artifact and
structured compile report.

These declarations are qualification policy, not target implementation. The
compiler owns architecture lowering and emission; kernel packages merely name
the profiles they promise to support.

## Map

| Path | Contents |
| --- | --- |
| [`amdgpu/`](amdgpu/) | AMDGPU HAL artifact profiles. |
