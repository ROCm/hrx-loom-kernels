# Copyright 2026 AMD ROCm™ Software
# SPDX-License-Identifier: MIT

"""Repository compile-report capture and comparison orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


CAPTURE_SCHEMA = "hrx-loom-kernels.compile-report-capture.v1"
WORKSPACE_SCHEMA = "hrx-loom-kernels.compile-report-workspace.v1"
LOOM_REPORT_TARGET = "@iree//loom/py/loom/tools:loom-compile-report"
_LOOM_COMPILATION_PROVIDER_SUFFIX = "%LoomCompilationInfo"
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")
_PROVIDER_QUERY_EXPRESSION = (
    '"\\n".join(['
    'str(target.label) + "\\t" + '
    'provider.artifact.path + "\\t" + '
    'provider.report.path + "\\t" + '
    'provider.target.backend + "\\t" + '
    "provider.target.target "
    "for key, provider in providers(target).items() "
    f'if key.endswith("{_LOOM_COMPILATION_PROVIDER_SUFFIX}")])'
)


class Error(Exception):
    """Reports an invalid capture request or evidence workspace."""


@dataclass(frozen=True)
class Compilation:
    """One target-qualified compilation exposed by LoomCompilationInfo."""

    label: str
    artifact_path: PurePosixPath
    report_path: PurePosixPath
    backend: str
    target: str


@dataclass(frozen=True)
class ReportTool:
    """The public compile-report executable used to interpret every capture."""

    path: Path
    sha256: str


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("+ " + shlex.join(command), flush=True)
    result = subprocess.run(
        command,
        capture_output=capture_output,
        check=False,
        cwd=cwd,
        text=True,
    )
    if check and result.returncode != 0:
        if capture_output:
            sys.stdout.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def _command_output(command: list[str], *, cwd: Path) -> str:
    result = _run_process(command, cwd=cwd, capture_output=True)
    return result.stdout or ""


def _bazel_output(
    bazel_executable: str,
    repository_root: Path,
    arguments: list[str],
) -> str:
    return _command_output([bazel_executable, *arguments], cwd=repository_root)


def _bazel(
    bazel_executable: str,
    repository_root: Path,
    arguments: list[str],
) -> None:
    _run_process([bazel_executable, *arguments], cwd=repository_root)


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


def _workspace_path(root: Path, relative_path: str) -> Path:
    pure_path = PurePosixPath(relative_path)
    if (
        not relative_path
        or pure_path.is_absolute()
        or pure_path.as_posix() != relative_path
        or any(part in (".", "..") for part in pure_path.parts)
    ):
        raise Error(f"Invalid path in compile-report evidence: {relative_path}")
    return root.joinpath(*pure_path.parts)


def initialize_workspace(repository_root: Path, output_root: Path) -> None:
    """Initializes one dedicated evidence workspace."""
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    try:
        repository_relative = output_root.relative_to(repository_root)
    except ValueError:
        repository_relative = None
    if repository_relative is not None and (
        not repository_relative.parts or repository_relative.parts[0] != ".notes"
    ):
        raise Error(
            "A compile-report workspace inside the repository must be below "
            f".notes/: {output_root}"
        )

    if output_root.exists() and not output_root.is_dir():
        raise Error(f"Compile-report output path is not a directory: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    marker_path = output_root / "workspace.json"
    if not marker_path.exists():
        if any(output_root.iterdir()):
            raise Error(
                "Compile-report output directory is not an initialized evidence "
                f"workspace: {output_root}"
            )
        _write_json_atomic(marker_path, {"schema": WORKSPACE_SCHEMA})
        return
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Error(f"Cannot read compile-report workspace: {marker_path}") from error
    if marker != {"schema": WORKSPACE_SCHEMA}:
        raise Error(f"Unsupported compile-report workspace: {marker_path}")


def _normalize_main_label(label: str) -> str:
    if label.startswith("@@//"):
        label = label[2:]
    elif label.startswith("@//"):
        label = label[1:]
    _label_parts(label)
    return label


def _label_parts(label: str) -> tuple[PurePosixPath, str]:
    package, separator, target = label.removeprefix("//").partition(":")
    package_path = PurePosixPath(package)
    if (
        not label.startswith("//")
        or not separator
        or (package and package_path.as_posix() != package)
        or any(part in (".", "..") for part in package_path.parts)
        or not target
        or target in (".", "..")
        or "/" in target
        or "\\" in target
    ):
        raise Error(
            "Compilation targets must be explicit main-repository labels with "
            f"package-local rule names: {label}"
        )
    return package_path, target


def _execution_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in (".", "..") for part in path.parts)
    ):
        raise Error(f"Invalid Bazel execution path: {value}")
    return path


def _target_expression(target_patterns: list[str]) -> str:
    patterns = sorted(set(target_patterns or ["//..."]))
    if any(not pattern for pattern in patterns):
        raise Error("Compile-report target patterns must not be empty")
    return "set(" + " ".join(json.dumps(pattern) for pattern in patterns) + ")"


def discover_compilations(
    repository_root: Path,
    target_patterns: list[str],
    bazel_options: list[str],
    bazel_executable: str,
) -> list[Compilation]:
    """Discovers public LoomCompilationInfo leaves in a configured scope."""
    output = _bazel_output(
        bazel_executable,
        repository_root,
        [
            "cquery",
            _target_expression(target_patterns),
            *bazel_options,
            "--output=starlark",
            f"--starlark:expr={_PROVIDER_QUERY_EXPRESSION}",
        ],
    )
    compilations = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            raise Error(f"Invalid LoomCompilationInfo cquery row: {line}")
        label, artifact_path, report_path, backend, target = fields
        if not backend:
            raise Error(f"Compilation target has no backend: {label}")
        compilations.append(
            Compilation(
                label=_normalize_main_label(label),
                artifact_path=_execution_path(artifact_path),
                report_path=_execution_path(report_path),
                backend=backend,
                target=target,
            )
        )
    if not compilations:
        patterns = ", ".join(sorted(set(target_patterns or ["//..."])))
        raise Error(f"No Loom compilations match Bazel target patterns: {patterns}")
    labels = [compilation.label for compilation in compilations]
    if len(set(labels)) != len(labels):
        raise Error("Bazel returned duplicate Loom compilation labels")
    return sorted(compilations, key=lambda compilation: compilation.label)


def build_compilations(
    repository_root: Path,
    compilations: list[Compilation],
    bazel_options: list[str],
    bazel_executable: str,
) -> Path:
    """Builds selected leaves and returns their Bazel execution root."""
    _bazel(
        bazel_executable,
        repository_root,
        [
            "build",
            *bazel_options,
            *[compilation.label for compilation in compilations],
        ],
    )
    execution_root_text = _bazel_output(
        bazel_executable,
        repository_root,
        ["info", *bazel_options, "execution_root"],
    )
    execution_root_lines = [
        line.strip() for line in execution_root_text.splitlines() if line.strip()
    ]
    if len(execution_root_lines) != 1:
        raise Error("Bazel did not report exactly one execution root")
    execution_root = Path(execution_root_lines[0])
    if not execution_root.is_absolute() or not execution_root.is_dir():
        raise Error(f"Bazel execution root does not exist: {execution_root}")
    for compilation in compilations:
        for kind, relative_path in (
            ("artifact", compilation.artifact_path),
            ("report", compilation.report_path),
        ):
            path = execution_root.joinpath(*relative_path.parts)
            if not path.is_file():
                raise Error(
                    f"Bazel did not materialize {kind} for {compilation.label}: {path}"
                )
    return execution_root


def resolve_report_tool(
    repository_root: Path,
    bazel_options: list[str],
    bazel_executable: str,
) -> ReportTool:
    """Builds and resolves the public loom-compile-report executable."""
    _bazel(
        bazel_executable,
        repository_root,
        ["build", *bazel_options, LOOM_REPORT_TARGET],
    )
    executable_path = _bazel_output(
        bazel_executable,
        repository_root,
        [
            "cquery",
            LOOM_REPORT_TARGET,
            *bazel_options,
            "--output=starlark",
            "--starlark:expr=target.files_to_run.executable.path",
        ],
    )
    executable_lines = [
        line.strip() for line in executable_path.splitlines() if line.strip()
    ]
    if len(executable_lines) != 1:
        raise Error("The loom-compile-report target did not resolve one executable")
    relative_executable = _execution_path(executable_lines[0])
    execution_root_text = _bazel_output(
        bazel_executable,
        repository_root,
        ["info", *bazel_options, "execution_root"],
    )
    execution_root_lines = [
        line.strip() for line in execution_root_text.splitlines() if line.strip()
    ]
    if len(execution_root_lines) != 1:
        raise Error("Bazel did not report exactly one execution root")
    path = Path(execution_root_lines[0]).joinpath(*relative_executable.parts)
    if not path.is_file():
        raise Error(f"Bazel did not materialize loom-compile-report: {path}")
    return ReportTool(path=path, sha256=_sha256(path))


def _resolve_commit(repository_root: Path, reference: str) -> str:
    if not reference:
        raise Error("Compile-report base reference must not be empty")
    output = _command_output(
        ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"],
        cwd=repository_root,
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1 or _COMMIT_PATTERN.fullmatch(lines[0]) is None:
        raise Error(f"Git did not resolve one commit for base {reference!r}")
    return lines[0]


def _path_record(repository_root: Path, relative_path: str) -> dict[str, Any]:
    path = _workspace_path(repository_root, relative_path)
    if not os.path.lexists(path):
        return {"kind": "missing", "path": relative_path}
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return {
            "kind": "symlink",
            "mode": mode,
            "path": relative_path,
            "target": os.readlink(path),
        }
    if not stat.S_ISREG(metadata.st_mode):
        raise Error(f"Unsupported dirty repository path: {path}")
    return {
        "kind": "file",
        "mode": mode,
        "path": relative_path,
        "sha256": _sha256(path),
    }


def working_tree_identity(repository_root: Path) -> dict[str, Any]:
    """Describes the exact tracked commit plus build-visible dirty paths."""
    head = _resolve_commit(repository_root, "HEAD")
    changed_output = _command_output(
        ["git", "diff", "--name-only", "-z", "HEAD", "--"],
        cwd=repository_root,
    )
    untracked_output = _command_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repository_root,
    )
    paths = sorted(
        {
            path
            for path in (*changed_output.split("\0"), *untracked_output.split("\0"))
            if path
        }
    )
    records = [_path_record(repository_root, path) for path in paths]
    return {
        "commit": head,
        "dirty": bool(records),
        "dirty_path_count": len(records),
        "dirty_paths": paths,
        "tree_sha256": _json_digest({"commit": head, "records": records}),
        "type": "working_tree",
    }


def _tracked_file_identity(repository_root: Path, relative_path: str) -> dict[str, str]:
    path = repository_root / relative_path
    if not path.is_file():
        return {"path": relative_path, "state": "absent"}
    return {"path": relative_path, "sha256": _sha256(path), "state": "present"}


def toolchain_identity(repository_root: Path) -> dict[str, Any]:
    """Records repository-controlled Bazel and Loom selection inputs."""
    return {
        "files": [
            _tracked_file_identity(repository_root, relative_path)
            for relative_path in (
                ".bazelrc",
                ".bazelrc.local",
                ".bazelversion",
                "MODULE.bazel",
                "MODULE.bazel.lock",
            )
        ]
    }


def materialize_base_source(
    repository_root: Path,
    output_root: Path,
    reference: str,
) -> tuple[Path, dict[str, Any]]:
    """Materializes an immutable Git base without creating a worktree."""
    commit = _resolve_commit(repository_root, reference)
    local_bazelrc = repository_root / ".bazelrc.local"
    local_bazelrc_sha256 = _sha256(local_bazelrc) if local_bazelrc.is_file() else None
    source_key = {
        "commit": commit,
        "local_bazelrc_sha256": local_bazelrc_sha256,
    }
    source_id = _json_digest(source_key)
    sources_root = output_root / "sources"
    sources_root.mkdir(parents=True, exist_ok=True)
    source_root = sources_root / source_id
    metadata_path = sources_root / f"{source_id}.json"
    metadata = {
        "schema": WORKSPACE_SCHEMA,
        "source_id": source_id,
        **source_key,
    }
    if source_root.exists() or metadata_path.exists():
        if not source_root.is_dir() or not metadata_path.is_file():
            raise Error(f"Incomplete archived base source workspace: {source_root}")
        try:
            existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise Error(
                f"Cannot read archived source metadata: {metadata_path}"
            ) from error
        if existing_metadata != metadata:
            raise Error(f"Archived source identity mismatch: {source_root}")
        return source_root, {
            "commit": commit,
            "reference": reference,
            "source_id": source_id,
            "type": "git_commit",
        }

    temporary_root = sources_root / f".{source_id}.{uuid.uuid4().hex}.tmp"
    archive_path = sources_root / f".{source_id}.{uuid.uuid4().hex}.tar"
    temporary_root.mkdir()
    try:
        _run_process(
            [
                "git",
                "archive",
                "--format=tar",
                f"--output={archive_path}",
                commit,
            ],
            cwd=repository_root,
        )
        _run_process(
            [
                "tar",
                "--extract",
                f"--file={archive_path}",
                f"--directory={temporary_root}",
            ],
            cwd=repository_root,
        )
        if local_bazelrc.is_file():
            shutil.copyfile(local_bazelrc, temporary_root / ".bazelrc.local")
        temporary_root.replace(source_root)
        _write_json_atomic(metadata_path, metadata)
    finally:
        if archive_path.exists():
            archive_path.unlink()
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
    return source_root, {
        "commit": commit,
        "reference": reference,
        "source_id": source_id,
        "type": "git_commit",
    }


def _show_report(report_tool: ReportTool, report_path: Path) -> dict[str, Any]:
    output = _command_output(
        [str(report_tool.path), "show", str(report_path), "--format=json"],
        cwd=report_path.parent,
    )
    try:
        view = json.loads(output)
    except json.JSONDecodeError as error:
        raise Error(
            f"loom-compile-report returned invalid JSON for {report_path}"
        ) from error
    if (
        not isinstance(view, dict)
        or view.get("kind") != "loom.compile_report.show"
        or not isinstance(view.get("entries"), list)
    ):
        raise Error(f"loom-compile-report returned an invalid show view: {report_path}")
    return view


def _capture_relative_paths(
    compilation: Compilation,
) -> tuple[PurePosixPath, PurePosixPath, PurePosixPath]:
    package, target = _label_parts(compilation.label)
    artifact_name = compilation.artifact_path.name
    report_name = compilation.report_path.name
    if not artifact_name or not report_name:
        raise Error(f"Compilation outputs require filenames: {compilation.label}")
    return (
        PurePosixPath("artifacts", *package.parts, artifact_name),
        PurePosixPath("reports", *package.parts, report_name),
        PurePosixPath("views", *package.parts, f"{target}.show.json"),
    )


def _load_capture(output_root: Path, capture_id: str) -> dict[str, Any]:
    capture_root = output_root / "captures" / capture_id
    manifest_path = capture_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Error(f"Cannot read compile-report capture: {manifest_path}") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != CAPTURE_SCHEMA
        or manifest.get("capture_id") != capture_id
        or not isinstance(manifest.get("entries"), list)
    ):
        raise Error(f"Invalid compile-report capture manifest: {manifest_path}")
    for entry in manifest["entries"]:
        if not isinstance(entry, dict):
            raise Error(f"Invalid compile-report capture entry: {manifest_path}")
        for name in ("artifact", "report"):
            record = entry.get(name)
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("path"), str)
                or not isinstance(record.get("sha256"), str)
            ):
                raise Error(f"Invalid {name} record in capture: {manifest_path}")
            path = _workspace_path(capture_root, record["path"])
            if not path.is_file() or _sha256(path) != record["sha256"]:
                raise Error(f"Corrupt {name} in compile-report capture: {path}")
        view = entry.get("view")
        if (
            not isinstance(view, dict)
            or not isinstance(view.get("path"), str)
            or not isinstance(view.get("sha256"), str)
        ):
            raise Error(f"Invalid public show view in capture: {manifest_path}")
        view_path = _workspace_path(capture_root, view["path"])
        if not view_path.is_file() or _sha256(view_path) != view["sha256"]:
            raise Error(f"Missing public show view in capture: {manifest_path}")
    return manifest


def capture_repository(
    repository_root: Path,
    output_root: Path,
    source_identity: dict[str, Any],
    target_patterns: list[str],
    configs: list[str],
    report_tool: ReportTool,
    bazel_executable: str,
) -> dict[str, Any]:
    """Builds and immutably captures one configured compilation corpus."""
    if any(not configuration for configuration in configs):
        raise Error("Compile-report Bazel configurations must not be empty")
    bazel_options = ["-c", "opt", *[f"--config={value}" for value in configs]]
    compilations = discover_compilations(
        repository_root,
        target_patterns,
        bazel_options,
        bazel_executable,
    )
    execution_root = build_compilations(
        repository_root,
        compilations,
        bazel_options,
        bazel_executable,
    )

    entry_inputs = []
    capture_paths: set[str] = set()
    for compilation in compilations:
        artifact_path = execution_root.joinpath(*compilation.artifact_path.parts)
        report_path = execution_root.joinpath(*compilation.report_path.parts)
        artifact_relative, report_relative, view_relative = _capture_relative_paths(
            compilation
        )
        relative_paths = {
            artifact_relative.as_posix(),
            report_relative.as_posix(),
            view_relative.as_posix(),
        }
        if capture_paths & relative_paths:
            raise Error(f"Compilation capture paths collide: {compilation.label}")
        capture_paths.update(relative_paths)
        entry_inputs.append(
            {
                "artifact": {
                    "path": artifact_relative.as_posix(),
                    "sha256": _sha256(artifact_path),
                    "source": artifact_path,
                },
                "backend": compilation.backend,
                "label": compilation.label,
                "report": {
                    "path": report_relative.as_posix(),
                    "sha256": _sha256(report_path),
                    "source": report_path,
                },
                "target": compilation.target,
                "view": view_relative.as_posix(),
            }
        )

    invocation = {
        "bazel_options": bazel_options,
        "configs": list(configs),
        "target_patterns": sorted(set(target_patterns or ["//..."])),
    }
    capture_key_entries = [
        {
            "artifact": {
                "path": entry["artifact"]["path"],
                "sha256": entry["artifact"]["sha256"],
            },
            "backend": entry["backend"],
            "label": entry["label"],
            "report": {
                "path": entry["report"]["path"],
                "sha256": entry["report"]["sha256"],
            },
            "target": entry["target"],
            "view": entry["view"],
        }
        for entry in entry_inputs
    ]
    capture_key = {
        "entries": capture_key_entries,
        "invocation": invocation,
        "report_tool": {
            "label": LOOM_REPORT_TARGET,
            "sha256": report_tool.sha256,
        },
        "source": source_identity,
        "toolchain": toolchain_identity(repository_root),
    }
    capture_id = _json_digest(capture_key)
    capture_root = output_root / "captures" / capture_id
    if capture_root.exists():
        return _load_capture(output_root, capture_id)

    captures_root = output_root / "captures"
    captures_root.mkdir(parents=True, exist_ok=True)
    temporary_root = captures_root / f".{capture_id}.{uuid.uuid4().hex}.tmp"
    temporary_root.mkdir()
    try:
        manifest_entries = []
        for entry in entry_inputs:
            artifact_destination = _workspace_path(
                temporary_root, entry["artifact"]["path"]
            )
            report_destination = _workspace_path(
                temporary_root, entry["report"]["path"]
            )
            artifact_destination.parent.mkdir(parents=True, exist_ok=True)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry["artifact"]["source"], artifact_destination)
            shutil.copyfile(entry["report"]["source"], report_destination)
            if _sha256(artifact_destination) != entry["artifact"]["sha256"]:
                raise Error(f"Artifact changed while capturing: {entry['label']}")
            if _sha256(report_destination) != entry["report"]["sha256"]:
                raise Error(f"Report changed while capturing: {entry['label']}")
            view = _show_report(report_tool, report_destination)
            view_destination = _workspace_path(temporary_root, entry["view"])
            _write_json_atomic(view_destination, view)
            manifest_entries.append(
                {
                    "artifact": {
                        "path": entry["artifact"]["path"],
                        "sha256": entry["artifact"]["sha256"],
                    },
                    "backend": entry["backend"],
                    "label": entry["label"],
                    "report": {
                        "path": entry["report"]["path"],
                        "sha256": entry["report"]["sha256"],
                    },
                    "target": entry["target"],
                    "view": {
                        "path": entry["view"],
                        "sha256": _sha256(view_destination),
                    },
                }
            )
        captured_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        manifest = {
            "capture_id": capture_id,
            "captured_at": captured_at,
            "entries": manifest_entries,
            "invocation": invocation,
            "report_tool": capture_key["report_tool"],
            "schema": CAPTURE_SCHEMA,
            "source": source_identity,
            "toolchain": capture_key["toolchain"],
        }
        _write_json_atomic(temporary_root / "manifest.json", manifest)
        temporary_root.replace(capture_root)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
    return _load_capture(output_root, capture_id)
