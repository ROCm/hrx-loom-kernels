"""Repository policy wrappers around the public Loom build rules."""

load("@iree//build_tools/bazel:python.bzl", "iree_py_test")
load(
    "@iree//loom/build_tools/bazel:defs.bzl",
    _loom_compile = "loom_compile",
    _loom_compile_target = "loom_compile_target",
    _loom_execution_profile = "loom_execution_profile",
    _loom_kernel_library = "loom_kernel_library",
    _loom_library = "loom_library",
    _loom_test_library = "loom_test_library",
    _loom_tools_toolchains = "loom_tools_toolchains",
)

loom_compile = _loom_compile
loom_compile_target = _loom_compile_target
loom_execution_profile = _loom_execution_profile
loom_tools_toolchains = _loom_tools_toolchains

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
    is_ggml_compatibility = _is_package_or_subpackage(package, "kernel/ggml")
    for dep in deps:
        dep_package = _label_package(dep, package)
        if dep_package == None:
            continue
        if is_ggml_compatibility and _is_package_or_subpackage(dep_package, "kernel"):
            fail(
                (
                    "//%s is a GGML compatibility package and cannot depend on " +
                    "kernel package %s; compatibility kernels are leaf launch surfaces"
                ) % (package, dep),
            )
        if not is_ggml_compatibility and _is_package_or_subpackage(
            dep_package,
            "kernel/ggml",
        ):
            fail(
                (
                    "//%s is a native kernel package and cannot depend on GGML " +
                    "compatibility package %s; depend on its motif/ foundations instead"
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
        _declare_source_policy_test(name, "motif", srcs, tags)

def loom_kernel_library(
        name,
        srcs,
        deps = [],
        compile_targets = [],
        tags = [],
        visibility = None):
    """Declares a launchable kernel package with enforced layer dependencies."""
    package = native.package_name()
    if not _is_package_or_subpackage(package, "kernel"):
        fail("loom_kernel_library must be declared below kernel/, got //%s" % package)
    _validate_kernel_dependencies(package, deps)
    _loom_kernel_library(
        name = name,
        srcs = srcs,
        deps = deps,
        compile_targets = compile_targets,
        tags = tags,
        visibility = visibility,
    )
    _declare_source_policy_test(name, "kernel", srcs, tags)

def loom_test_library(
        name,
        srcs,
        deps = [],
        compile_targets = [],
        execution_profiles = [],
        tags = [],
        visibility = None):
    """Declares private wrapper programs in an explicit test package."""
    package = native.package_name()
    if "test" not in package.split("/"):
        fail(
            "loom_test_library must be declared below an explicit test/ " +
            "package, got //%s" % package,
        )
    if _is_package_or_subpackage(package, "motif"):
        _validate_motif_dependencies(package, deps)
    _loom_test_library(
        name = name,
        srcs = srcs,
        deps = deps,
        compile_targets = compile_targets,
        execution_profiles = execution_profiles,
        tags = tags,
        visibility = visibility,
    )
    _declare_source_policy_test(name, "test", srcs, tags)
