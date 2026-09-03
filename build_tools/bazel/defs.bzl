"""Repository policy wrappers around the public Loom build rules."""

load("@hrx//build_tools/bazel:python.bzl", "iree_py_test")
load(
    "@hrx//loom/build_tools/bazel:defs.bzl",
    _loom_kernel_library = "loom_kernel_library",
    _loom_library = "loom_library",
    _loom_test = "loom_test",
)

_BENCHMARK_MODULE_TAG = "loom-benchmark-module"
_BENCHMARK_MODULE_SUFFIX = "_benchmark_module"
_MOTIF_ADMISSION_TAG = "loom-admission-motif"
_MOTIF_ADMISSION_SUFFIX = "_admission_motif"

def _is_package_or_subpackage(package, root):
    return package == root or package.startswith(root + "/")

def _label_package(label, current_package):
    label_text = str(label)
    if label_text.startswith("@"):
        return None
    if label_text.startswith("//"):
        return label_text[2:].split(":", 1)[0]
    return current_package

def _validate_motif_dependencies(package, deps):
    for dep in deps:
        dep_package = _label_package(dep, package)
        if dep_package != None and _is_package_or_subpackage(dep_package, "kernel"):
            fail(
                (
                    "//%s is a motif package and cannot depend on kernel package %s; " +
                    "move the reusable representation or algorithm into motif/"
                ) % (package, dep),
            )

def _validate_kernel_dependencies(package, deps):
    for dep in deps:
        dep_package = _label_package(dep, package)
        if dep_package == None:
            continue
        if _is_package_or_subpackage(dep_package, "experimental"):
            continue
        if _is_package_or_subpackage(dep_package, "kernel"):
            fail(
                (
                    "//%s is a kernel package and cannot depend on kernel package %s; " +
                    "move reusable code into motif/ and compose launch surfaces above kernel/"
                ) % (package, dep),
            )
        if not _is_package_or_subpackage(dep_package, "motif"):
            fail(
                (
                    "//%s is a kernel package and can depend only on motif/ " +
                    "libraries, got %s"
                ) % (package, dep),
            )

def _declare_source_policy_test(name, layer, srcs, tags):
    text_srcs = [src for src in srcs if str(src).endswith(".loom")]
    if not text_srcs:
        fail("%s requires at least one .loom text source" % name)
    iree_py_test(
        name = name + "_source_policy_test",
        srcs = ["//build_tools/bazel:source_policy.py"],
        main = "source_policy.py",
        args = ["--layer=" + layer] + [
            "$(location %s)" % src
            for src in text_srcs
        ],
        data = text_srcs,
        tags = tags + ["hostonly"],
        visibility = ["//visibility:private"],
    )

def _declare_benchmark_module(name, module, tags):
    native.filegroup(
        name = name + _BENCHMARK_MODULE_SUFFIX,
        srcs = [module],
        tags = tags + [_BENCHMARK_MODULE_TAG],
        testonly = True,
        visibility = ["//visibility:private"],
    )

def _declare_motif_admission(name, tags):
    native.filegroup(
        name = name + _MOTIF_ADMISSION_SUFFIX,
        srcs = [":" + name],
        tags = tags + [_MOTIF_ADMISSION_TAG],
        visibility = ["//visibility:private"],
    )

def _validate_execution_profiles(name, execution_profiles):
    if not execution_profiles:
        fail("%s requires at least one execution profile" % name)

def _validate_kernel_qualification(name, targets, execution_profiles):
    if not targets:
        fail("%s requires at least one target profile" % name)
    _validate_execution_profiles(name, execution_profiles)

def loom_motif_library(
        name,
        srcs = [],
        deps = [],
        tags = [],
        visibility = None):
    """Declares a reusable motif package with no launchable kernel surface."""
    package = native.package_name()
    if not _is_package_or_subpackage(package, "motif"):
        fail("loom_motif_library must be declared below motif/, got //%s" % package)
    _validate_motif_dependencies(package, deps)
    _loom_library(
        name = name,
        srcs = srcs,
        deps = deps,
        tags = tags,
        visibility = visibility,
    )
    if srcs:
        _declare_motif_admission(name, tags)
        _declare_source_policy_test(name, "motif", srcs, tags)

def loom_kernel_library(
        name,
        srcs,
        deps = [],
        targets = [],
        execution_profiles = [],
        tags = [],
        visibility = None):
    """Declares a launchable kernel package with enforced layer dependencies."""
    package = native.package_name()
    if not _is_package_or_subpackage(package, "kernel"):
        fail("loom_kernel_library must be declared below kernel/, got //%s" % package)
    _validate_kernel_dependencies(package, deps)
    _validate_kernel_qualification(name, targets, execution_profiles)
    _loom_kernel_library(
        name = name,
        srcs = srcs,
        deps = deps,
        execution_profiles = execution_profiles,
        targets = targets,
        tags = tags,
        visibility = visibility,
    )
    _declare_benchmark_module(
        name,
        ":" + name + "_test_module",
        tags,
    )
    _declare_source_policy_test(name, "kernel", srcs, tags)

def loom_test(
        name,
        srcs,
        deps = [],
        args = [],
        execution_profile = None,
        size = "small",
        tags = [],
        visibility = None):
    """Declares private wrapper programs in an explicit test package."""
    package = native.package_name()
    if "test" not in package.split("/"):
        fail(
            "loom_test must be declared below an explicit test/ " +
            "package, got //%s" % package,
        )
    if _is_package_or_subpackage(package, "motif"):
        _validate_motif_dependencies(package, deps)
    if execution_profile == None:
        fail("%s requires an execution profile" % name)
    _loom_test(
        name = name,
        srcs = srcs,
        args = args,
        deps = deps,
        execution_profile = execution_profile,
        size = size,
        tags = tags,
        visibility = visibility,
    )
    _declare_benchmark_module(
        name,
        ":" + name + "_module",
        tags,
    )
    _declare_source_policy_test(name, "test", srcs, tags)
