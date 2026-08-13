# Contributing to the Loom standard library

This repository admits reusable compiler inputs, not a catalog of model ports. A contribution earns its place by strengthening a composable motif, exposing a deliberate launch or program ABI, or qualifying a reusable target profile. The unit of review is the complete library closure: source, documentation, correctness programs, benchmark plans, target qualification, and the dependency edges that make it reusable.

## Admission surfaces

| Surface | Ownership | Admission evidence |
| --- | --- | --- |
| `motif/` | Target-neutral data representations and algorithms expressed as `func.def`, `func.template`, encodings, and related declarations. | Canonical and lint-clean source, scoped documentation, no launch surface, and reachability from a qualified kernel or test leaf. |
| `kernel/` | Concrete `kernel.def` entry points implementing one operation contract and ABI. | A semantic benchmark plan, at least one compile target, at least one execution profile, and dependencies confined to production motifs. |
| `motif/**/test/` and `kernel/**/test/` | Test-only wrapper programs used when production code has no natural launch witness. | Private/test-only archive, semantic benchmarks and correctness cases, at least one compile target, and at least one execution profile. |
| `target/` | Reusable build-time compile and execution profiles. | Target requirements, runner resources, and tool arguments contained in the target package rather than common rules. |
| `model/` | Composition roots for model and command programs. | A repository-owned model admission rule must define archive, planning, execution, and deployment behavior before source enters this layer. |

Source-bearing `model/` and `target/` packages currently fail repository policy. That failure is intentional: raw `.loom` files cannot bypass the same build and evidence contract applied to motifs and kernels. A new source surface begins with its repository rule and production lifecycle.

## Branch-local incubation

Long-running ports and kernel searches may live below `experimental/` on
feature branches. Experimental packages use the public Loom and Bazel rules;
normal `//...` builds, tests, target-profile execution queries, formatting, and
benchmark discovery traverse them. This gives incubation work the same compiler
and hardware evidence as admitted code without turning it into a weaker
standard-library layer.

Pull requests and pushes targeting the repository release line fail while the
candidate tree tracks any `experimental/**` path. The gate reads the candidate
tree rather than branch history, so a branch may carry experiments for as long
as needed, extract reusable work into `motif/`, `kernel/`, `model/`, or
`target/`, and remove the incubation payload before promotion. Feature branches
may compose experimental targets with sources at any repository path; those
paths are working locations until the candidate tree passes release admission.
Promotion moves the reusable sources into their durable layer, and the ordinary
complete build proves that no required experimental package was left behind.

## Source contract

Each `.loom` file stands on its own as an implementation artifact. Its leading documentation establishes the represented format or operation, public symbol contract, parameter and shape meaning, storage layout, ABI ownership, and any specification or oracle used to establish behavior. Package READMEs carry information that cannot be recovered from `ls`: scope boundaries, external specifications, ABI relationships, implementation status, and the intended composition layer. Per-symbol inventories remain beside the source so they cannot drift from it.

Public symbols are the intended linking surface. Helpers whose only consumers are in the same module remain private. Motifs expose representation and algorithm contracts without a launch ABI; GGML-compatible kernels own GGML graph-operation ABI details, while native kernels may consume the same GGUF carriers without inheriting that ABI.

Dynamic workloads and shapes remain values whenever the implementation can support them. Configuration represents facts that materially select the compiled program—model architecture choices, formats, or target capabilities—not convenient constants such as one observed element count or workgroup size. Production motifs do not declare configuration; their functions and templates receive values and facts from composition roots so one linked motif can serve every invocation in a command program. Test-only wrappers may declare case configuration when it is part of the execution witness. Range facts on `config.decl`, `index.assume`, `buffer.assume`, and precise view types carry caller knowledge into specialization. Target-specific source belongs in a target-scoped motif only when the algorithm truly cannot be expressed from target facts supplied at the leaf.

Canonical text and authoring policy are independent gates. `loom-format` parses, verifies, and canonicalizes syntax. `loom-lint` checks human-facing source conventions such as semantic constant names. Repository policy then verifies layer placement, dependency direction, explicit BUILD declarations, scoped documentation, and qualification closure.

## Correctness and benchmark programs

A `check.case` is an executable statement of behavior, not merely sample data. Cases cover the contracts most likely to be lost during specialization: signedness, byte and lane order, extrema, tails, non-power-of-two extents, dynamic dimensions, aliasing, and numerical tolerance. Expected values come from a transparent reference or independently established oracle.

Keeping a production kernel and its cases in one `.loom` file is the shortest iteration loop: a reproducer can be formatted, planned, compiled, and executed without a build-system edit. Larger packages may split implementation and checks into separate source files in the same library. Motifs use test-only wrapper kernels only when no production kernel already exercises the same carrier; a second wrapper that duplicates an existing launch surface adds code without adding evidence.

Every kernel and test leaf is benchmark-planned by the public Loom runner. An archive with no `check.benchmark` fails planning. Benchmarks name correctness cases and select workloads that expose meaningful operating regimes rather than one flattering shape. Decode, small-batch, prefill, tails, and format-specific stress shapes are separate benchmark declarations when they exercise different specialization or hardware behavior.

At least one compile target proves that the linked program lowers through a real backend. At least one execution profile proves the public cases through a real runner. Profiles remain reusable and target-owned; common library rules contain no gfx, Vulkan, HIP, or other device-specific conditionals. Additional targets strengthen portability without recompiling target-neutral motif archives independently for every leaf.

## Performance evidence

Correctness admission and performance claims are separate. Shared CI establishes that programs build and execute; its changing machines and load cannot establish a regression threshold. Performance evidence comes from a stable, identified machine with baseline and candidate captured under the same software, target, workload, measurement mode, and exclusive-use discipline.

`python dev.py benchmark` writes immutable, package-shaped artifact bundles using the public benchmark report schema. A focused claim names the benchmark declarations, device, baseline revision, candidate revision, and relevant distribution rather than quoting one best iteration. Compiler changes include the corresponding compile-report comparison so register, LDS, spill, occupancy, and emitted-code changes remain attributable. Roofline proxies and oracle implementations bound the search space; dispatch-count reduction alone is not evidence that a fusion improved end-to-end execution.

## Local admission sequence

```shell
python dev.py setup --release
python dev.py format --check
python dev.py lint
python dev.py test
python dev.py build
```

`dev.py lint` checks filesystem policy and asks Bazel to prove that every source-bearing motif reaches a benchmarkable kernel or test leaf. `dev.py test` runs generated format, public lint, benchmark-plan, target-compilation, and available execution tests. Hardware tests without a matching local resource skip locally and execute through the repository runner matrix.

A focused performance capture adds an explicit target and device:

```shell
python dev.py benchmark \
  --config=amdgpu \
  --device=amdgpu://0 \
  --output-dir=/tmp/hrx-loom-kernels-candidate \
  --target=//kernel/ggml/quantize:q8_1_x4_f32 \
  -- \
  --measure=dispatch_complete \
  --batch-size=64 \
  --profile-final-batch=true
```

Review centers on the boundary questions: whether the code lives at the lowest reusable layer, whether its public symbols and ABI are deliberate, whether specialization receives all available facts without freezing incidental shapes, whether correctness cases cover the dangerous contracts, whether target qualifications exercise the intended backend, and whether any performance claim is bounded by reproducible baseline and oracle evidence.
