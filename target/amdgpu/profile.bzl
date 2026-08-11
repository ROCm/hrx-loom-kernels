"""AMDGPU execution profiles for Loom correctness programs."""

load(
    "@iree//loom/requirements:defs.bzl",
    "EMIT_AMDGPU",
    "EXECUTE_IREE_HAL",
    "TARGET_ARCH_AMDGPU",
)
load(
    "@iree//runtime/requirements:defs.bzl",
    "AMDGPU_RESOURCE",
    "HAL_AMDGPU",
)
load("//build_tools/bazel:defs.bzl", "loom_execution_profile")

AMDGPU_HARDWARE = loom_execution_profile(
    name = "amdgpu_hardware",
    build_requirements = [
        TARGET_ARCH_AMDGPU,
        EMIT_AMDGPU,
        EXECUTE_IREE_HAL,
        HAL_AMDGPU,
    ],
    executor = "hardware",
    resource_group = "loom-amdgpu-tests",
    run_requirements = [AMDGPU_RESOURCE],
    runner_args = ["--device=amdgpu"],
    target_class = "gpu",
    target_family = "amdgpu",
)
