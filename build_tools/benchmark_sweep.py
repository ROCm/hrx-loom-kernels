# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Dependency-aware local benchmark sweep orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


LOOM_BENCHMARK_TARGET = (
    "@iree//loom/src/loom/tools/iree-benchmark-loom:iree-benchmark-loom"
)
LOOM_BENCHMARK_TAG = "loom-benchmark-module"
LOOM_BENCHMARK_ROOTS = ("kernel", "model", "motif")
SWEEP_SCHEMA = "hrx-loom-kernels.benchmark-sweep.v1"
_EXECUTION_ENVIRONMENT_PREFIXES = (
    "CUDA_",
    "GPU_",
    "HIP_",
    "HSA_",
    "IREE_",
    "METAL_",
    "ONEAPI_",
    "ROCR_",
    "VK_",
    "VULKAN_",
)
_EXECUTION_ENVIRONMENT_NAMES = ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")


class Error(Exception):
    """Reports an invalid sweep request or workspace."""


@dataclass(frozen=True)
class BenchmarkModule:
    """One selected benchmark archive materialized by Bazel."""

    selection_label: str
    label: str
    path: Path


@dataclass(frozen=True)
class SourceClosure:
    """Configured main-repository dependency closure for one selection."""

    dependencies: tuple[str, ...]
    sources: tuple[str, ...]


BazelCommand = Callable[..., str]
RunCommand = Callable[..., str]


def _normalize_cquery_label(label: str) -> str:
    if label.startswith("@@//"):
        return label[2:]
    if label.startswith("@//"):
        return label[1:]
    return label


def _target_expression(targets: list[str]) -> str:
    return "set(" + " ".join(json.dumps(target) for target in targets) + ")"


def benchmark_query_expression(repository_root: Path) -> str:
    scopes = [
        f"//{root}/..."
        for root in LOOM_BENCHMARK_ROOTS
        if any((repository_root / root).rglob("BUILD.bazel"))
    ]
    if not scopes:
        raise Error("No benchmark library packages exist in this checkout")
    scope_expression = " union ".join(scopes)
    return f'attr(tags, "{LOOM_BENCHMARK_TAG}", {scope_expression})'


def discover_targets(repository_root: Path, bazel: BazelCommand) -> list[str]:
    output = bazel(
        ["query", benchmark_query_expression(repository_root), "--output=label"],
        capture_output=True,
    )
    targets = sorted(line.strip() for line in output.splitlines() if line.strip())
    if not targets:
        raise Error("No benchmark modules are declared in this checkout")
    return targets


def bundle_relative_path(label: str) -> Path:
    package, separator, target = label.removeprefix("//").partition(":")
    package_path = PurePosixPath(package)
    if (
        not label.startswith("//")
        or not separator
        or not package
        or package_path.as_posix() != package
        or any(part in (".", "..") for part in package_path.parts)
        or not target
        or target in (".", "..")
        or "/" in target
        or "\\" in target
    ):
        raise Error(
            "Benchmark targets must be explicit main-repository labels with "
            f"package-local rule names: {label}"
        )
    return Path(*package_path.parts) / target


def validate_targets(targets: list[str]) -> list[str]:
    normalized_targets = sorted(set(targets))
    for target in normalized_targets:
        bundle_relative_path(target)
    return normalized_targets


def resolve_modules(
    repository_root: Path,
    targets: list[str],
    bazel_options: list[str],
    bazel: BazelCommand,
) -> list[BenchmarkModule]:
    output = bazel(
        [
            "cquery",
            _target_expression(targets),
            *bazel_options,
            "--output=starlark",
            "--starlark:expr="
            'str(target.label) + "\\t" + "\\t".join(['
            'str(file.owner) + "\\t" + file.path '
            "for file in target.files.to_list()])",
        ],
        capture_output=True,
    )
    modules = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not fields[2].endswith(".loombc"):
            raise Error(
                "Each benchmark selection must forward exactly one .loombc archive"
            )
        selection_label = _normalize_cquery_label(fields[0])
        label = _normalize_cquery_label(fields[1])
        module_path = repository_root / fields[2]
        if not module_path.is_file():
            raise Error(
                f"Bazel did not materialize benchmark module {label}: {module_path}"
            )
        modules.append(BenchmarkModule(selection_label, label, module_path))

    selected_labels = {_normalize_cquery_label(target) for target in targets}
    resolved_labels = {module.selection_label for module in modules}
    if resolved_labels != selected_labels or len(modules) != len(targets):
        raise Error(
            "Benchmark target resolution changed the selected targets: "
            f"selected {sorted(selected_labels)}, resolved {sorted(resolved_labels)}"
        )
    owner_labels = [module.label for module in modules]
    if len(set(owner_labels)) != len(owner_labels):
        raise Error("Multiple benchmark selections resolved to the same archive")
    return sorted(modules, key=lambda module: module.label)


def resolve_runner(
    repository_root: Path,
    bazel_options: list[str],
    bazel: BazelCommand,
) -> Path:
    output = bazel(
        [
            "cquery",
            LOOM_BENCHMARK_TARGET,
            *bazel_options,
            "--output=files",
        ],
        capture_output=True,
    )
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    if len(paths) != 1:
        raise Error("The iree-benchmark-loom target must emit exactly one executable")
    runner_path = repository_root / paths[0]
    if not runner_path.is_file():
        raise Error(f"Bazel did not materialize the benchmark runner: {runner_path}")
    return runner_path


def _parse_source_closures(
    cquery_payload: dict[str, Any], targets: list[str]
) -> dict[str, SourceClosure]:
    results = cquery_payload.get("results")
    if not isinstance(results, list):
        raise Error("Bazel cquery did not return configured dependency results")

    rule_inputs: dict[str, set[str]] = {}
    source_labels: set[str] = set()
    for result in results:
        target = result.get("target", {})
        target_type = target.get("type")
        if target_type == "RULE":
            rule = target.get("rule", {})
            name = rule.get("name")
            inputs = rule.get("ruleInput", [])
            if not isinstance(name, str) or not isinstance(inputs, list):
                raise Error("Bazel cquery returned an invalid configured rule")
            normalized_name = _normalize_cquery_label(name)
            normalized_inputs = {
                _normalize_cquery_label(value)
                for value in inputs
                if isinstance(value, str)
            }
            rule_inputs.setdefault(normalized_name, set()).update(normalized_inputs)
        elif target_type == "SOURCE_FILE":
            name = target.get("sourceFile", {}).get("name")
            if isinstance(name, str):
                source_labels.add(_normalize_cquery_label(name))

    closures = {}
    for target in targets:
        root = _normalize_cquery_label(target)
        if root not in rule_inputs:
            raise Error(f"Bazel cquery omitted selected benchmark target {root}")
        visited: set[str] = set()
        dependencies: set[str] = set()
        sources: set[str] = set()
        pending = [root]
        while pending:
            label = pending.pop()
            if label in visited:
                continue
            visited.add(label)
            if label in source_labels:
                if label.startswith("//") and label.endswith(".loom"):
                    sources.add(label)
                continue
            if label != root and label.startswith("//"):
                dependencies.add(label)
            pending.extend(rule_inputs.get(label, ()))
        if not sources:
            raise Error(f"Benchmark target {root} has no configured .loom sources")
        closures[root] = SourceClosure(
            dependencies=tuple(sorted(dependencies)),
            sources=tuple(sorted(sources)),
        )
    return closures


def query_source_closures(
    targets: list[str],
    bazel_options: list[str],
    bazel: BazelCommand,
) -> dict[str, SourceClosure]:
    output = bazel(
        [
            "cquery",
            f"deps({_target_expression(targets)})",
            *bazel_options,
            "--output=jsonproto",
            "--noimplicit_deps",
            "--notool_deps",
            "--noinclude_aspects",
            "--proto:include_configurations=true",
            "--proto:locations=false",
            "--proto:default_values=false",
            "--proto:output_rule_attrs=srcs,deps,tags",
        ],
        capture_output=True,
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise Error("Bazel cquery returned invalid dependency JSON") from error
    return _parse_source_closures(payload, targets)


def _source_path(repository_root: Path, label: str) -> Path:
    package, separator, target = label.removeprefix("//").partition(":")
    package_path = PurePosixPath(package)
    target_path = PurePosixPath(target)
    if (
        not label.startswith("//")
        or not separator
        or package_path.as_posix() != package
        or target_path.as_posix() != target
        or target_path.is_absolute()
        or any(
            part in (".", "..") for part in (*package_path.parts, *target_path.parts)
        )
        or target_path.suffix != ".loom"
    ):
        raise Error(f"Invalid main-repository Loom source label from cquery: {label}")
    path = repository_root.joinpath(*package_path.parts, *target_path.parts)
    if not path.is_file():
        raise Error(f"Configured Loom source does not exist: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _new_run_identity() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    run_id = now.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    return run_id, timestamp


def _host_identity() -> dict[str, str]:
    return {
        "node": platform.node(),
        "platform": platform.platform(),
    }


def _environment_identity() -> dict[str, Any]:
    values = {
        name: value
        for name, value in os.environ.items()
        if name in _EXECUTION_ENVIRONMENT_NAMES
        or name.startswith(_EXECUTION_ENVIRONMENT_PREFIXES)
    }
    return {
        "names": sorted(values),
        "sha256": _json_digest(values),
    }


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            json.dump(payload, file, indent=2, sort_keys=True)
            file.write("\n")
            temporary_path = Path(file.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _load_index(output_root: Path) -> dict[str, Any]:
    if output_root.exists() and not output_root.is_dir():
        raise Error(f"Benchmark output path is not a directory: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "index.json"
    if not index_path.exists():
        contents = list(output_root.iterdir())
        if contents:
            raise Error(
                "Benchmark output directory is not an initialized sweep workspace: "
                f"{output_root}"
            )
        payload = {"schema": SWEEP_SCHEMA, "entries": {}}
        _write_json_atomic(index_path, payload)
        return payload
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Error(f"Cannot read benchmark workspace index: {index_path}") from error
    if payload.get("schema") != SWEEP_SCHEMA or not isinstance(
        payload.get("entries"), dict
    ):
        raise Error(f"Unsupported benchmark workspace index: {index_path}")
    if not all(isinstance(entry, dict) for entry in payload["entries"].values()):
        raise Error(f"Invalid benchmark workspace entries: {index_path}")
    return payload


def _workspace_path(output_root: Path, relative_path: str) -> Path:
    pure_path = PurePosixPath(relative_path)
    if (
        not relative_path
        or pure_path.is_absolute()
        or pure_path.as_posix() != relative_path
        or any(part in (".", "..") for part in pure_path.parts)
    ):
        raise Error(f"Invalid path in benchmark workspace index: {relative_path}")
    return output_root.joinpath(*pure_path.parts)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _source_record(repository_root: Path, label: str) -> dict[str, str]:
    path = _source_path(repository_root, label)
    return {
        "label": label,
        "path": path.relative_to(repository_root).as_posix(),
        "sha256": _sha256(path),
    }


def _source_digest_map(sources: Any) -> dict[str, str]:
    if not isinstance(sources, list):
        raise Error("Invalid source records in benchmark workspace index")
    result = {}
    for source in sources:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("label"), str)
            or not isinstance(source.get("sha256"), str)
        ):
            raise Error("Invalid source record in benchmark workspace index")
        result[source["label"]] = source["sha256"]
    return result


def _source_changes(
    previous_sources: Any, current_sources: list[dict[str, str]]
) -> list[dict[str, str]]:
    previous = _source_digest_map(previous_sources)
    current = _source_digest_map(current_sources)
    changes = []
    for label in sorted(previous.keys() | current.keys()):
        if label not in previous:
            changes.append({"label": label, "change": "added"})
        elif label not in current:
            changes.append({"label": label, "change": "removed"})
        elif previous[label] != current[label]:
            changes.append({"label": label, "change": "modified"})
    return changes


def _cached_evidence_exists(output_root: Path, entry: dict[str, Any]) -> bool:
    bundle = entry.get("artifact_bundle")
    result = entry.get("results")
    if not isinstance(bundle, str) or not isinstance(result, str):
        return False
    bundle_path = _workspace_path(output_root, bundle)
    result_path = _workspace_path(output_root, result)
    return (
        bundle_path.is_dir()
        and (bundle_path / "manifest.json").is_file()
        and result_path.is_file()
    )


def _read_bundle_index(
    output_root: Path, bundle_path: Path
) -> tuple[str, list[str], int]:
    if not (bundle_path / "manifest.json").is_file():
        raise Error(f"Benchmark runner did not write a bundle manifest: {bundle_path}")
    result_paths = [
        path
        for path in (bundle_path / "results.json", bundle_path / "results.jsonl")
        if path.is_file()
    ]
    if len(result_paths) != 1:
        raise Error(
            "Benchmark runner must write exactly one native result file under "
            f"{bundle_path}"
        )
    result_path = result_paths[0]
    benchmarks: set[str] = set()
    work_item_count = 0
    try:
        if result_path.suffix == ".json":
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            logical_benchmarks = payload.get("benchmarks", [])
            work_items = payload.get("work_items", [])
            if isinstance(logical_benchmarks, list):
                for benchmark in logical_benchmarks:
                    if isinstance(benchmark, dict) and isinstance(
                        benchmark.get("benchmark"), str
                    ):
                        benchmarks.add(benchmark["benchmark"])
            if isinstance(work_items, list):
                work_item_count = len(work_items)
        else:
            work_item_indexes: set[int] = set()
            for line in result_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                benchmark = row.get("benchmark")
                if isinstance(benchmark, str):
                    benchmarks.add(benchmark)
                benchmark_result = row.get("benchmark_result")
                if isinstance(benchmark_result, dict) and isinstance(
                    benchmark_result.get("benchmark"), str
                ):
                    benchmarks.add(benchmark_result["benchmark"])
                work_item_index = row.get("work_item_index")
                if isinstance(work_item_index, int):
                    work_item_indexes.add(work_item_index)
            work_item_count = len(work_item_indexes)
    except (OSError, json.JSONDecodeError) as error:
        raise Error(f"Cannot index native benchmark result: {result_path}") from error
    return (
        result_path.relative_to(output_root).as_posix(),
        sorted(benchmarks),
        work_item_count,
    )


def _validate_runner_args(arguments: list[str]) -> None:
    owned_flags = {
        "--artifact-bundle-dir",
        "--device",
        "--file-output-dir",
        "--output",
        "--profile-artifacts-dir",
    }
    for argument in arguments:
        flag = argument.split("=", 1)[0]
        if flag in owned_flags:
            raise Error(f"dev.py benchmark owns the {flag} flag")


def _cache_entry(module_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_bundle": module_entry["artifact_bundle"],
        "benchmarks": module_entry["benchmarks"],
        "dependencies": module_entry["dependencies"],
        "execution_key": module_entry["execution_key"],
        "invocation_sha256": module_entry["invocation_sha256"],
        "module_sha256": module_entry["module"]["sha256"],
        "results": module_entry["results"],
        "run_id": module_entry["artifact_run_id"],
        "runner_sha256": module_entry["runner_sha256"],
        "sources": module_entry["sources"],
        "work_item_count": module_entry["work_item_count"],
    }


def _finalize_workspace(
    output_root: Path,
    index: dict[str, Any],
    run_id: str,
    created_at: str,
    invocation: dict[str, Any],
    runner_path: Path,
    runner_sha256: str,
    modules: list[dict[str, Any]],
    status: str,
) -> Path:
    completed_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    summary = {
        "artifact_changed_module_count": sum(
            bool(module["artifact_changed"]) for module in modules
        ),
        "attempted_module_count": sum(bool(module["attempted"]) for module in modules),
        "executed_module_count": sum(
            module["state"] == "executed" for module in modules
        ),
        "failed_module_count": sum(module["state"] == "failed" for module in modules),
        "not_run_module_count": sum(module["state"] == "not_run" for module in modules),
        "potentially_affected_module_count": sum(
            bool(module["potentially_affected"]) for module in modules
        ),
        "reused_module_count": sum(module["state"] == "reused" for module in modules),
        "selected_module_count": len(modules),
    }
    manifest = {
        "completed_at": completed_at,
        "created_at": created_at,
        "invocation": invocation,
        "modules": modules,
        "run_id": run_id,
        "runner": {
            "path": str(runner_path),
            "sha256": runner_sha256,
        },
        "schema": SWEEP_SCHEMA,
        "status": status,
        "summary": summary,
    }
    run_manifest_path = output_root / "runs" / f"{run_id}.json"
    if run_manifest_path.exists():
        raise Error(f"Benchmark run manifest already exists: {run_manifest_path}")
    _write_json_atomic(run_manifest_path, manifest)

    updated_entries = dict(index["entries"])
    for module in modules:
        if module["state"] in ("executed", "reused"):
            updated_entries[module["label"]] = _cache_entry(module)
    _write_json_atomic(
        output_root / "index.json",
        {"entries": updated_entries, "schema": SWEEP_SCHEMA},
    )
    _write_json_atomic(output_root / "latest.json", manifest)
    return run_manifest_path


def run(
    args: Any,
    *,
    repository_root: Path,
    bazel: BazelCommand,
    run_command: RunCommand,
) -> None:
    runner_args = list(args.benchmark_args)
    if runner_args and runner_args[0] == "--":
        runner_args = runner_args[1:]
    _validate_runner_args(runner_args)

    output_root = Path(args.output_dir).expanduser()
    if not output_root.is_absolute():
        output_root = repository_root / output_root
    output_root = output_root.absolute()
    index = _load_index(output_root)

    targets = (
        validate_targets(args.targets)
        if args.targets
        else discover_targets(repository_root, bazel)
    )
    bazel_options = [
        "-c",
        "opt",
        *[f"--config={configuration}" for configuration in args.configs],
    ]
    bazel(["build", *bazel_options, LOOM_BENCHMARK_TARGET, *targets])
    modules = resolve_modules(repository_root, targets, bazel_options, bazel)
    closures = query_source_closures(targets, bazel_options, bazel)
    runner_path = resolve_runner(repository_root, bazel_options, bazel)
    runner_sha256 = _sha256(runner_path)
    run_id, created_at = _new_run_identity()

    execution_invocation = {
        "configs": list(args.configs),
        "device": args.device,
        "environment": _environment_identity(),
        "host": _host_identity(),
        "runner_args": runner_args,
    }
    invocation = {
        **execution_invocation,
        "targets": targets,
    }
    invocation_sha256 = _json_digest(execution_invocation)
    source_records = {
        label: _source_record(repository_root, label)
        for label in sorted(
            {label for closure in closures.values() for label in closure.sources}
        )
    }
    module_entries: list[dict[str, Any]] = []
    modules_by_label: dict[str, BenchmarkModule] = {}
    for module in modules:
        modules_by_label[module.label] = module
        closure = closures[module.selection_label]
        sources = [source_records[label] for label in closure.sources]
        dependencies = [
            label for label in closure.dependencies if label != module.label
        ]
        module_sha256 = _sha256(module.path)
        cached = index["entries"].get(module.label)
        previous_sources = cached.get("sources", []) if cached else []
        changed_sources = _source_changes(previous_sources, sources)
        artifact_changed = (
            cached is None or cached.get("module_sha256") != module_sha256
        )
        runner_changed = cached is None or cached.get("runner_sha256") != runner_sha256
        invocation_changed = (
            cached is None or cached.get("invocation_sha256") != invocation_sha256
        )
        execution_key = _json_digest(
            {
                "invocation_sha256": invocation_sha256,
                "label": module.label,
                "module_sha256": module_sha256,
                "runner_sha256": runner_sha256,
            }
        )
        evidence_available = bool(cached) and _cached_evidence_exists(
            output_root, cached
        )
        reusable = (
            not args.rerun_all
            and bool(cached)
            and cached.get("execution_key") == execution_key
            and evidence_available
        )
        reasons = []
        if cached is None:
            reasons.append("new_module")
        else:
            if changed_sources:
                reasons.append("source_changed")
            if artifact_changed:
                reasons.append("module_changed")
            if runner_changed:
                reasons.append("runner_changed")
            if invocation_changed:
                reasons.append("invocation_changed")
            if not evidence_available:
                reasons.append("cached_evidence_missing")
        if args.rerun_all:
            reasons.append("rerun_all")

        entry: dict[str, Any] = {
            "artifact_bundle": None,
            "artifact_changed": artifact_changed,
            "artifact_run_id": None,
            "attempted": False,
            "benchmarks": [],
            "changed_sources": changed_sources,
            "dependencies": dependencies,
            "executed": False,
            "execution_key": execution_key,
            "invocation_sha256": invocation_sha256,
            "label": module.label,
            "module": {
                "path": _relative_path(module.path, repository_root),
                "sha256": module_sha256,
            },
            "potentially_affected": bool(reasons),
            "previous_artifact_bundle": (
                cached.get("artifact_bundle") if cached is not None else None
            ),
            "reasons": reasons,
            "results": None,
            "reused_from_run_id": None,
            "runner_sha256": runner_sha256,
            "selection_label": module.selection_label,
            "sources": sources,
            "state": "pending",
            "work_item_count": 0,
        }
        if reusable:
            entry.update(
                {
                    "artifact_bundle": cached["artifact_bundle"],
                    "artifact_run_id": cached["run_id"],
                    "benchmarks": cached.get("benchmarks", []),
                    "results": cached["results"],
                    "reused_from_run_id": cached["run_id"],
                    "state": "reused",
                    "work_item_count": cached.get("work_item_count", 0),
                }
            )
        else:
            bundle_path = (
                output_root / "artifacts" / run_id / bundle_relative_path(module.label)
            )
            entry["artifact_bundle"] = bundle_path.relative_to(output_root).as_posix()
            entry["artifact_run_id"] = run_id
        module_entries.append(entry)

    affected_count = sum(entry["potentially_affected"] for entry in module_entries)
    scheduled_count = sum(entry["state"] == "pending" for entry in module_entries)
    print(
        f"Selected {len(module_entries)} benchmark modules: "
        f"{affected_count} potentially affected, {scheduled_count} scheduled, "
        f"{len(module_entries) - scheduled_count} reusable",
        flush=True,
    )

    failure: Exception | None = None
    for entry in module_entries:
        if entry["state"] == "reused":
            qualifier = (
                " after source-only change" if entry["potentially_affected"] else ""
            )
            print(f"REUSE {entry['label']}{qualifier}", flush=True)
            continue
        module = modules_by_label[entry["label"]]
        bundle_path = _workspace_path(output_root, entry["artifact_bundle"])
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        print(
            f"RUN   {entry['label']} ({', '.join(entry['reasons'])})",
            flush=True,
        )
        entry["attempted"] = True
        try:
            run_command(
                [
                    str(runner_path),
                    str(module.path),
                    *runner_args,
                    f"--device={args.device}",
                    f"--artifact-bundle-dir={bundle_path}",
                ]
            )
            results, benchmarks, work_item_count = _read_bundle_index(
                output_root, bundle_path
            )
            entry.update(
                {
                    "benchmarks": benchmarks,
                    "executed": True,
                    "results": results,
                    "state": "executed",
                    "work_item_count": work_item_count,
                }
            )
        except (Error, subprocess.CalledProcessError) as error:
            entry["state"] = "failed"
            entry["failure"] = {
                "message": str(error),
                "returncode": getattr(error, "returncode", None),
            }
            failure = error
            break

    if failure is not None:
        for entry in module_entries:
            if entry["state"] == "pending":
                entry["state"] = "not_run"
    status = "failed" if failure is not None else "ok"
    run_manifest_path = _finalize_workspace(
        output_root,
        index,
        run_id,
        created_at,
        invocation,
        runner_path,
        runner_sha256,
        module_entries,
        status,
    )
    executed_count = sum(entry["state"] == "executed" for entry in module_entries)
    reused_count = sum(entry["state"] == "reused" for entry in module_entries)
    print(
        f"Executed {executed_count} benchmark modules; reused {reused_count}. "
        f"Sweep manifest: {run_manifest_path}",
        flush=True,
    )
    if failure is not None:
        raise failure
