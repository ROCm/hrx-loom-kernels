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
COMPARISON_SCHEMA = "hrx-loom-kernels.compile-report-comparison.v1"
SOURCE_SCHEMA = "hrx-loom-kernels.compile-report-source.v1"
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
    """The public report executable and its project-owned implementation identity."""

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


def _seal_json(payload: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["content_sha256"] = _json_digest(payload)
    return sealed


def _validate_json_seal(payload: dict[str, Any], description: str) -> None:
    content_sha256 = payload.get("content_sha256")
    if not isinstance(content_sha256, str):
        raise Error(f"Unsealed {description}")
    contents = {key: value for key, value in payload.items() if key != "content_sha256"}
    if _json_digest(contents) != content_sha256:
        raise Error(f"Modified {description}")


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


def _normalize_catalog_label(label: str) -> str:
    if label.startswith("@@//"):
        label = label[2:]
    elif label.startswith("@//"):
        label = label[1:]
    if (
        not label.startswith("//")
        or "\\" in label
        or any(character.isspace() for character in label)
    ):
        raise Error(f"Invalid main-repository label from Bazel query: {label}")
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


def _canonical_target_pattern(pattern: str) -> str:
    if not pattern or not pattern.startswith("//") or "\\" in pattern:
        raise Error(f"Invalid main-repository Bazel target pattern: {pattern!r}")
    if pattern == "//...":
        return pattern
    if pattern.endswith("/..."):
        package = pattern[2:-4].rstrip("/")
        if any(part in ("", ".", "..") for part in package.split("/")):
            raise Error(f"Invalid recursive Bazel target pattern: {pattern}")
        return pattern
    if ":" not in pattern:
        package = pattern[2:].rstrip("/")
        if not package or any(part in ("", ".", "..") for part in package.split("/")):
            raise Error(f"Invalid Bazel target pattern: {pattern}")
        pattern = f"//{package}:{package.rsplit('/', 1)[-1]}"
    _, target = _label_parts(pattern)
    if target in ("all", "*"):
        return pattern
    return pattern


def _pattern_matches_catalog(pattern: str, catalog: set[str]) -> bool:
    if pattern == "//...":
        return bool(catalog)
    if pattern.endswith("/..."):
        package_prefix = pattern[2:-4].rstrip("/")
        return any(
            label.removeprefix("//").partition(":")[0] == package_prefix
            or label.removeprefix("//")
            .partition(":")[0]
            .startswith(package_prefix + "/")
            for label in catalog
        )
    _, target = _label_parts(pattern)
    if target in ("all", "*"):
        package_name = pattern.removeprefix("//").partition(":")[0]
        return any(
            label.removeprefix("//").partition(":")[0] == package_name
            for label in catalog
        )
    return pattern in catalog


def _available_target_patterns(
    repository_root: Path,
    target_patterns: list[str],
    bazel_executable: str,
) -> list[str]:
    patterns = sorted(
        {_canonical_target_pattern(pattern) for pattern in target_patterns or ["//..."]}
    )
    output = _bazel_output(
        bazel_executable,
        repository_root,
        ["query", "//...", "--output=label"],
    )
    catalog = {
        _normalize_catalog_label(line.strip())
        for line in output.splitlines()
        if line.strip()
    }
    return [
        pattern for pattern in patterns if _pattern_matches_catalog(pattern, catalog)
    ]


def _target_expression(target_patterns: list[str]) -> str:
    selected = (
        "set(" + " ".join(json.dumps(pattern) for pattern in target_patterns) + ")"
    )
    return f"rdeps(//..., {selected})"


def discover_compilations(
    repository_root: Path,
    target_patterns: list[str],
    bazel_options: list[str],
    bazel_executable: str,
) -> list[Compilation]:
    """Discovers public LoomCompilationInfo leaves in a configured scope."""
    available_patterns = _available_target_patterns(
        repository_root,
        target_patterns,
        bazel_executable,
    )
    if not available_patterns:
        return []
    output = _bazel_output(
        bazel_executable,
        repository_root,
        [
            "cquery",
            _target_expression(available_patterns),
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
    target_files = _bazel_output(
        bazel_executable,
        repository_root,
        [
            "cquery",
            LOOM_REPORT_TARGET,
            *bazel_options,
            "--output=starlark",
            "--starlark:expr="
            '"executable\\t" + target.files_to_run.executable.path + "\\n" + '
            '"\\n".join(["runfile\\t" + file.path '
            "for file in target.default_runfiles.files.to_list()])",
        ],
    )
    executable_paths = []
    runfile_paths = []
    for line in target_files.splitlines():
        if not line.strip():
            continue
        kind, separator, value = line.partition("\t")
        if not separator:
            raise Error("The loom-compile-report target returned an invalid file row")
        if kind == "executable":
            executable_paths.append(_execution_path(value))
        elif kind == "runfile":
            runfile_paths.append(_execution_path(value))
        else:
            raise Error(f"Unknown loom-compile-report file row: {kind}")
    if len(executable_paths) != 1:
        raise Error("The loom-compile-report target did not resolve one executable")
    relative_executable = executable_paths[0]
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
    executable_parts = relative_executable.parts
    try:
        external_index = executable_parts.index("external")
        runfiles_workspace = executable_parts[external_index + 1]
    except (ValueError, IndexError) as error:
        raise Error(
            "The loom-compile-report executable is not owned by an external "
            f"Loom repository: {relative_executable}"
        ) from error
    implementation_files = []
    runfiles_root = Path(str(path) + ".runfiles") / runfiles_workspace
    for runfile_path in runfile_paths:
        runfile_parts = runfile_path.parts
        if (
            len(runfile_parts) < 3
            or runfile_parts[0] != "external"
            or runfile_parts[1] != runfiles_workspace
        ):
            continue
        relative_path = PurePosixPath(*runfile_parts[2:])
        implementation_path = runfiles_root.joinpath(*relative_path.parts)
        if not implementation_path.is_file():
            raise Error(
                "Bazel did not materialize a Loom report-tool runfile: "
                f"{implementation_path}"
            )
        implementation_files.append(
            {
                "path": relative_path.as_posix(),
                "sha256": _sha256(implementation_path),
            }
        )
    if not implementation_files:
        raise Error("The loom-compile-report target has no project-owned runfiles")
    identity = {
        "executable_sha256": _sha256(path),
        "implementation_files": sorted(
            implementation_files, key=lambda file: file["path"]
        ),
    }
    return ReportTool(path=path, sha256=_json_digest(identity))


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


def _source_tree_identity(source_root: Path) -> dict[str, Any]:
    records = []
    bazel_output_links = {
        "bazel-bin",
        "bazel-out",
        "bazel-testlogs",
        f"bazel-{source_root.name}",
    }
    for path in sorted(source_root.rglob("*")):
        relative_path = path.relative_to(source_root)
        if (
            len(relative_path.parts) == 1
            and relative_path.name in bazel_output_links
            and path.is_symlink()
        ):
            continue
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode):
            record = {
                "kind": "symlink",
                "mode": mode,
                "path": relative_path.as_posix(),
                "target": os.readlink(path),
            }
        elif stat.S_ISREG(metadata.st_mode):
            record = {
                "kind": "file",
                "mode": mode,
                "path": relative_path.as_posix(),
                "sha256": _sha256(path),
            }
        else:
            raise Error(f"Unsupported path in archived source workspace: {path}")
        records.append(record)
    return {
        "entry_count": len(records),
        "sha256": _json_digest(records),
    }


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
        "schema": SOURCE_SCHEMA,
    }
    source_id = _json_digest(source_key)
    sources_root = output_root / "sources"
    sources_root.mkdir(parents=True, exist_ok=True)
    source_root = sources_root / source_id
    metadata_path = sources_root / f"{source_id}.json"
    if source_root.exists() or metadata_path.exists():
        if not source_root.is_dir() or not metadata_path.is_file():
            raise Error(f"Incomplete archived base source workspace: {source_root}")
        try:
            existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise Error(
                f"Cannot read archived source metadata: {metadata_path}"
            ) from error
        if not isinstance(existing_metadata, dict):
            raise Error(f"Invalid archived source metadata: {metadata_path}")
        _validate_json_seal(
            existing_metadata, f"archived source metadata {metadata_path}"
        )
        if (
            existing_metadata.get("source_id") != source_id
            or existing_metadata.get("schema") != SOURCE_SCHEMA
            or existing_metadata.get("commit") != commit
            or existing_metadata.get("local_bazelrc_sha256") != local_bazelrc_sha256
        ):
            raise Error(f"Archived source identity mismatch: {source_root}")
        current_tree = _source_tree_identity(source_root)
        if current_tree != existing_metadata.get("tree"):
            raise Error(f"Archived source workspace was modified: {source_root}")
        return source_root, {
            "commit": commit,
            "source_id": source_id,
            "tree": current_tree,
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
        tree_identity = _source_tree_identity(temporary_root)
        metadata = {
            "commit": commit,
            "local_bazelrc_sha256": local_bazelrc_sha256,
            "schema": SOURCE_SCHEMA,
            "source_id": source_id,
            "tree": tree_identity,
        }
        temporary_root.replace(source_root)
        _write_json_atomic(metadata_path, _seal_json(metadata))
    finally:
        if archive_path.exists():
            archive_path.unlink()
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
    return source_root, {
        "commit": commit,
        "source_id": source_id,
        "tree": tree_identity,
        "type": "git_commit",
    }


def _show_report(
    report_tool: ReportTool,
    report_path: Path,
    capture_root: Path,
) -> dict[str, Any]:
    try:
        source_argument = report_path.relative_to(capture_root).as_posix()
    except ValueError as error:
        raise Error(f"Compile report is outside its capture: {report_path}") from error
    output = _command_output(
        [str(report_tool.path), "show", source_argument, "--format=json"],
        cwd=capture_root,
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
    _validate_json_seal(manifest, f"compile-report capture {manifest_path}")
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
    execution_root = None
    if compilations:
        execution_root = build_compilations(
            repository_root,
            compilations,
            bazel_options,
            bazel_executable,
        )

    entry_inputs = []
    capture_paths: set[str] = set()
    for compilation in compilations:
        if execution_root is None:
            raise Error("Compilation outputs have no Bazel execution root")
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
            view = _show_report(report_tool, report_destination, temporary_root)
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
        _write_json_atomic(temporary_root / "manifest.json", _seal_json(manifest))
        temporary_root.replace(capture_root)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
    return _load_capture(output_root, capture_id)


def _capture_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise Error("Compile-report capture has no entries")
    entries_by_label = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
            raise Error("Compile-report capture has an invalid entry")
        label = entry["label"]
        if label in entries_by_label:
            raise Error(f"Compile-report capture repeats label: {label}")
        entries_by_label[label] = entry
    return entries_by_label


def _run_report_tool(
    report_tool: ReportTool,
    arguments: list[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return _run_process(
        [str(report_tool.path), *arguments],
        cwd=cwd,
        capture_output=True,
        check=False,
    )


def _parse_diff_view(output: str, label: str) -> dict[str, Any]:
    try:
        view = json.loads(output)
    except json.JSONDecodeError as error:
        raise Error(
            f"loom-compile-report returned invalid diff JSON for {label}"
        ) from error
    if (
        not isinstance(view, dict)
        or view.get("kind") != "loom.compile_report.diff"
        or not isinstance(view.get("entries"), list)
        or not isinstance(view.get("changed_entry_count"), int)
        or not isinstance(view.get("unchanged_entry_count"), int)
    ):
        raise Error(f"loom-compile-report returned an invalid diff view for {label}")
    return view


def _comparison_relative_path(label: str, suffix: str) -> PurePosixPath:
    package, target = _label_parts(label)
    return PurePosixPath("diffs", *package.parts, f"{target}.{suffix}")


def _write_text_atomic(path: Path, contents: str) -> None:
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
            file.write(contents)
            temporary_path = Path(file.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _metric_changes(diff_view: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for entry in diff_view["entries"]:
        if not isinstance(entry, dict):
            raise Error("loom-compile-report diff contains an invalid entry")
        identity = entry.get("identity")
        if not isinstance(identity, dict):
            raise Error("loom-compile-report diff entry has no identity")
        for evidence in ("artifact_facts", "compiler_analysis"):
            group = entry.get(evidence)
            if not isinstance(group, dict):
                raise Error(f"loom-compile-report diff entry has no {evidence} group")
            changed = group.get("changed")
            incomplete = group.get("incomplete")
            if not isinstance(changed, dict) or not isinstance(incomplete, dict):
                raise Error(
                    f"loom-compile-report diff entry has invalid {evidence} metrics"
                )
            for metric, values in sorted(changed.items()):
                if not isinstance(values, dict):
                    raise Error(f"Invalid changed metric {metric}")
                changes.append(
                    {
                        "entry": identity,
                        "evidence": evidence,
                        "metric": metric,
                        "state": "changed",
                        **values,
                    }
                )
            for metric, values in sorted(incomplete.items()):
                if not isinstance(values, dict):
                    raise Error(f"Invalid incomplete metric {metric}")
                changes.append(
                    {
                        "entry": identity,
                        "evidence": evidence,
                        "metric": metric,
                        "state": "incomplete",
                        **values,
                    }
                )
    return changes


def _format_signed(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:+g}"


def _format_comparison_summary(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    lines = [
        "Loom repository compile-report comparison",
        f"  base capture: {summary['base_capture_id']}",
        f"  candidate capture: {summary['candidate_capture_id']}",
        (
            "  corpus: "
            f"{counts['common']} common, {counts['added']} added, "
            f"{counts['removed']} removed"
        ),
        (
            "  comparison: "
            f"{counts['changed']} changed, {counts['unchanged']} unchanged, "
            f"{counts['incomparable']} incomparable"
        ),
        (
            "  semantic diff: "
            f"{counts['semantically_changed']} changed, "
            f"{counts['semantically_unchanged']} unchanged, "
            f"{counts['unchanged']} byte-identical skipped"
        ),
    ]
    for entry in summary["entries"]:
        state = entry["state"]
        if state == "unchanged":
            continue
        lines.append("")
        lines.append(f"{entry['label']}: {state}")
        if state == "changed":
            if entry["semantic_state"] == "unchanged":
                lines.append("  semantic diff: unchanged")
            for metric in entry["metrics"]:
                baseline = metric.get("baseline", "unavailable")
                candidate = metric.get("candidate", "unavailable")
                text = (
                    f"  {metric['evidence']}.{metric['metric']}: "
                    f"{baseline} -> {candidate}"
                )
                if "delta" in metric:
                    text += f" ({_format_signed(metric['delta'])})"
                lines.append(text)
            lines.append(f"  diff: {entry['diff_text']['path']}")
        elif state == "incomparable":
            lines.append(f"  error: {entry['error']}")
            lines.append(f"  stderr: {entry['stderr']['path']}")
    return "\n".join(lines) + "\n"


def _load_comparison(output_root: Path, comparison_id: str) -> dict[str, Any]:
    comparison_root = output_root / "comparisons" / comparison_id
    summary_path = comparison_root / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Error(f"Cannot read compile-report comparison: {summary_path}") from error
    if (
        not isinstance(summary, dict)
        or summary.get("schema") != COMPARISON_SCHEMA
        or summary.get("comparison_id") != comparison_id
        or not isinstance(summary.get("entries"), list)
    ):
        raise Error(f"Invalid compile-report comparison: {summary_path}")
    _validate_json_seal(summary, f"compile-report comparison {summary_path}")
    summary_text_path = comparison_root / "summary.txt"
    if not summary_text_path.is_file():
        raise Error(
            f"Compile-report comparison has no text summary: {summary_text_path}"
        )
    for entry in summary["entries"]:
        if not isinstance(entry, dict):
            raise Error(f"Invalid comparison entry: {summary_path}")
        for field in ("diff_json", "diff_text", "stderr"):
            record = entry.get(field)
            if record is None:
                continue
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("path"), str)
                or not isinstance(record.get("sha256"), str)
            ):
                raise Error(f"Invalid comparison sidecar record: {field}")
            sidecar_path = _workspace_path(comparison_root, record["path"])
            if not sidecar_path.is_file() or _sha256(sidecar_path) != record["sha256"]:
                raise Error(f"Corrupt comparison sidecar: {sidecar_path}")
    return summary


def compare_captures(
    output_root: Path,
    base_capture: dict[str, Any],
    candidate_capture: dict[str, Any],
    report_tool: ReportTool,
) -> dict[str, Any]:
    """Compares two captures through the public report CLI."""
    base_capture_id = base_capture.get("capture_id")
    candidate_capture_id = candidate_capture.get("capture_id")
    if not isinstance(base_capture_id, str) or not isinstance(
        candidate_capture_id, str
    ):
        raise Error("Cannot compare captures without stable identities")
    comparison_key = {
        "base_capture_id": base_capture_id,
        "candidate_capture_id": candidate_capture_id,
        "report_tool_sha256": report_tool.sha256,
    }
    comparison_id = _json_digest(comparison_key)
    comparison_root = output_root / "comparisons" / comparison_id
    if comparison_root.exists():
        return _load_comparison(output_root, comparison_id)

    base_root = output_root / "captures" / base_capture_id
    candidate_root = output_root / "captures" / candidate_capture_id
    base_entries = _capture_entries(base_capture)
    candidate_entries = _capture_entries(candidate_capture)
    all_labels = sorted(base_entries.keys() | candidate_entries.keys())
    common_labels = sorted(base_entries.keys() & candidate_entries.keys())
    temporary_root = (
        output_root / "comparisons" / f".{comparison_id}.{uuid.uuid4().hex}.tmp"
    )
    temporary_root.mkdir(parents=True)
    comparison_entries = []
    try:
        for label in all_labels:
            base_entry = base_entries.get(label)
            candidate_entry = candidate_entries.get(label)
            if base_entry is None:
                comparison_entries.append({"label": label, "state": "added"})
                continue
            if candidate_entry is None:
                comparison_entries.append({"label": label, "state": "removed"})
                continue
            report_changed = (
                base_entry["report"]["sha256"] != candidate_entry["report"]["sha256"]
            )
            artifact_changed = (
                base_entry["artifact"]["sha256"]
                != candidate_entry["artifact"]["sha256"]
            )
            if not report_changed and not artifact_changed:
                comparison_entries.append(
                    {
                        "artifact_changed": False,
                        "label": label,
                        "report_changed": False,
                        "state": "unchanged",
                    }
                )
                continue

            baseline_report = _workspace_path(base_root, base_entry["report"]["path"])
            candidate_report = _workspace_path(
                candidate_root, candidate_entry["report"]["path"]
            )
            baseline_argument = Path(
                os.path.relpath(baseline_report, temporary_root)
            ).as_posix()
            candidate_argument = Path(
                os.path.relpath(candidate_report, temporary_root)
            ).as_posix()
            json_relative = _comparison_relative_path(label, "json")
            text_relative = _comparison_relative_path(label, "txt")
            stderr_relative = _comparison_relative_path(label, "stderr.txt")
            json_result = _run_report_tool(
                report_tool,
                [
                    "diff",
                    baseline_argument,
                    candidate_argument,
                    "--format=json",
                ],
                cwd=temporary_root,
            )
            if json_result.returncode != 0:
                stderr = json_result.stderr or ""
                _write_text_atomic(
                    _workspace_path(temporary_root, stderr_relative.as_posix()),
                    stderr,
                )
                stderr_path = _workspace_path(
                    temporary_root, stderr_relative.as_posix()
                )
                comparison_entries.append(
                    {
                        "artifact_changed": artifact_changed,
                        "error": f"loom-compile-report exited {json_result.returncode}",
                        "label": label,
                        "report_changed": report_changed,
                        "state": "incomparable",
                        "stderr": {
                            "path": stderr_relative.as_posix(),
                            "sha256": _sha256(stderr_path),
                        },
                    }
                )
                continue

            diff_view = _parse_diff_view(json_result.stdout or "", label)
            text_result = _run_report_tool(
                report_tool,
                ["diff", baseline_argument, candidate_argument],
                cwd=temporary_root,
            )
            if text_result.returncode != 0:
                raise Error(
                    "loom-compile-report produced a JSON diff but failed to render "
                    f"its text view for {label}"
                )
            json_path = _workspace_path(temporary_root, json_relative.as_posix())
            text_path = _workspace_path(temporary_root, text_relative.as_posix())
            _write_json_atomic(json_path, diff_view)
            _write_text_atomic(text_path, text_result.stdout or "")
            semantic_state = (
                "changed" if diff_view["changed_entry_count"] else "unchanged"
            )
            comparison_entries.append(
                {
                    "artifact_changed": artifact_changed,
                    "diff_json": {
                        "path": json_relative.as_posix(),
                        "sha256": _sha256(json_path),
                    },
                    "diff_text": {
                        "path": text_relative.as_posix(),
                        "sha256": _sha256(text_path),
                    },
                    "label": label,
                    "metrics": _metric_changes(diff_view),
                    "report_changed": report_changed,
                    "semantic_state": semantic_state,
                    "state": "changed",
                }
            )

        counts = {
            state: sum(entry["state"] == state for entry in comparison_entries)
            for state in ("added", "changed", "incomparable", "removed", "unchanged")
        }
        counts["common"] = len(common_labels)
        counts["artifact_changed"] = sum(
            bool(entry.get("artifact_changed")) for entry in comparison_entries
        )
        counts["report_changed"] = sum(
            bool(entry.get("report_changed")) for entry in comparison_entries
        )
        counts["semantically_changed"] = sum(
            entry.get("semantic_state") == "changed" for entry in comparison_entries
        )
        counts["semantically_unchanged"] = sum(
            entry.get("semantic_state") == "unchanged" for entry in comparison_entries
        )
        summary = {
            "base_capture_id": base_capture_id,
            "candidate_capture_id": candidate_capture_id,
            "comparison_id": comparison_id,
            "counts": counts,
            "entries": comparison_entries,
            "report_tool": {
                "label": LOOM_REPORT_TARGET,
                "sha256": report_tool.sha256,
            },
            "schema": COMPARISON_SCHEMA,
        }
        summary = _seal_json(summary)
        _write_json_atomic(temporary_root / "summary.json", summary)
        _write_text_atomic(
            temporary_root / "summary.txt", _format_comparison_summary(summary)
        )
        temporary_root.replace(comparison_root)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
    return _load_comparison(output_root, comparison_id)


def _write_latest(
    output_root: Path,
    base_reference: str | None,
    candidate_capture: dict[str, Any],
    comparison: dict[str, Any] | None,
) -> None:
    latest = {
        "base_reference": base_reference,
        "candidate_capture": candidate_capture,
        "comparison": comparison,
        "schema": WORKSPACE_SCHEMA,
    }
    _write_json_atomic(output_root / "latest.json", latest)


def run(
    args: Any,
    *,
    repository_root: Path,
    bazel_executable: str,
) -> None:
    """Runs one candidate capture and optional exact-base comparison."""
    if args.base is not None and not args.base:
        raise Error("Compile-report base reference must not be empty")
    configs = list(args.configs)
    if any(not configuration for configuration in configs):
        raise Error("Compile-report Bazel configurations must not be empty")
    target_patterns = sorted(
        {
            _canonical_target_pattern(pattern)
            for pattern in list(args.targets) or ["//..."]
        }
    )

    output_root = Path(args.output_dir).expanduser()
    if not output_root.is_absolute():
        output_root = repository_root / output_root
    output_root = output_root.resolve()
    initialize_workspace(repository_root, output_root)

    bazel_options = ["-c", "opt", *[f"--config={value}" for value in configs]]
    report_tool = resolve_report_tool(
        repository_root,
        bazel_options,
        bazel_executable,
    )
    candidate_identity = working_tree_identity(repository_root)
    candidate_capture = capture_repository(
        repository_root,
        output_root,
        candidate_identity,
        target_patterns,
        configs,
        report_tool,
        bazel_executable,
    )
    print(
        f"Captured {len(candidate_capture['entries'])} compilations as "
        f"{candidate_capture['capture_id']}",
        flush=True,
    )

    comparison = None
    if args.base is not None:
        base_root, base_identity = materialize_base_source(
            repository_root,
            output_root,
            args.base,
        )
        base_report_tool = resolve_report_tool(
            base_root,
            bazel_options,
            bazel_executable,
        )
        if base_report_tool.sha256 != report_tool.sha256:
            raise Error(
                "Base and candidate resolve different loom-compile-report tools; "
                "compare one compiler identity or update the tracked Loom pin"
            )
        base_capture = capture_repository(
            base_root,
            output_root,
            base_identity,
            target_patterns,
            configs,
            base_report_tool,
            bazel_executable,
        )
        if not base_capture["entries"] and not candidate_capture["entries"]:
            patterns = ", ".join(sorted(set(target_patterns or ["//..."])))
            raise Error(f"No Loom compilations match target patterns: {patterns}")
        comparison = compare_captures(
            output_root,
            base_capture,
            candidate_capture,
            report_tool,
        )
        summary_path = (
            output_root / "comparisons" / comparison["comparison_id"] / "summary.txt"
        )
        print(summary_path.read_text(encoding="utf-8"), end="")
        print(
            "Queryable summary: "
            + str(
                output_root
                / "comparisons"
                / comparison["comparison_id"]
                / "summary.json"
            ),
            flush=True,
        )
    elif not candidate_capture["entries"]:
        patterns = ", ".join(sorted(set(target_patterns or ["//..."])))
        raise Error(f"No Loom compilations match target patterns: {patterns}")
    else:
        print(
            "Queryable capture: "
            + str(
                output_root
                / "captures"
                / candidate_capture["capture_id"]
                / "manifest.json"
            ),
            flush=True,
        )
    _write_latest(output_root, args.base, candidate_capture, comparison)
