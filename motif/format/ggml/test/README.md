# GGML Format Motif Tests

These private programs wrap target-neutral GGML carrier functions in deliberate
launch and buffer boundaries. Production modules contain only the motifs in
the parent package; the wrappers, cases, and microbenchmarks remain test-only
and become roots in one linked test module. Production motifs participate only
as dependency libraries, so their own test-only symbols cannot leak into this
test's execution surface.

Direct-carrier cases isolate bit-field interpretation and selector ordering.
Record-level cases additionally cover the byte addressing, complete blocks,
and logical tails required by representative consumers. Physical AMDGPU runs
exercise target lowering and memory access, while the same authored cases can
serve as deterministic reference programs on targets that implement their
vector operations.
