#!/usr/bin/env python3
"""Build only the pinned T1 message package in a project-local ROS workspace. No robot I/O."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "fe2e186c867391ec78b301accdc91f0260ad0ff3"
CMAKE = """cmake_minimum_required(VERSION 3.16)
project(aimdk_msgs VERSION 1.0.0)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
foreach(dep std_msgs geometry_msgs sensor_msgs nav_msgs builtin_interfaces)
  find_package(${dep} REQUIRED)
endforeach()
set(interface_files)
set(base "${CMAKE_CURRENT_BINARY_DIR}/rosidl_interfaces")
foreach(kind msg srv action)
  file(GLOB_RECURSE interfaces "${CMAKE_CURRENT_SOURCE_DIR}/*.${kind}")
  foreach(interface ${interfaces})
    get_filename_component(name "${interface}" NAME)
    configure_file("${interface}" "${base}/${kind}/${name}" COPYONLY)
    list(APPEND interface_files "${base}:${kind}/${name}")
  endforeach()
endforeach()
rosidl_generate_interfaces(${PROJECT_NAME} ${interface_files}
  DEPENDENCIES std_msgs geometry_msgs sensor_msgs nav_msgs builtin_interfaces)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="T1 厂家 SDK Git checkout")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/sdk-build")
    args = parser.parse_args()
    source = args.source.resolve()
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != COMMIT:
        parser.error("SDK commit 不匹配；需要 " + COMMIT)
    if not Path("/opt/ros/humble/setup.bash").exists() or not shutil.which("colcon"):
        parser.error("需要 Ubuntu 22.04、ROS Humble 和 colcon；依赖清单见 README")
    workspace = args.output.resolve()
    if workspace.exists():
        parser.error(str(workspace) + " 已存在，不覆盖既有构建；请另选 --output")
    package = workspace / "src/aimdk_msgs"
    shutil.copytree(source / "aimdk_msgs", package, symlinks=False)
    for definition in (ROOT / "vendor_additions/aimdk_msgs/msg").glob("*.msg"):
        target = package / "hal/audio/msg" / definition.name
        if not target.exists():
            shutil.copyfile(definition, target)
    # Normalize the vendor's nested definitions into Humble's msg/srv/action layout.
    (package / "CMakeLists.txt").write_text(CMAKE)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ROS_", "RMW_", "AMENT_", "COLCON_", "FASTRTPS_", "FASTDDS_", "CONDA_"))
        and k
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "CMAKE_PREFIX_PATH",
            "VIRTUAL_ENV",
            "BASH_ENV",
            "ENV",
            "CYCLONEDDS_URI",
        }
    }
    env.update(PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", PYTHONNOUSERSITE="1")
    subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "source /opt/ros/humble/setup.bash && exec colcon build --packages-select aimdk_msgs "
            "--executor sequential --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3.10 "
            "-DPYTHON_EXECUTABLE=/usr/bin/python3.10 -DCMAKE_BUILD_TYPE=Release",
        ],
        cwd=workspace,
        env=env,
        check=True,
    )
    prefix = workspace / "install/aimdk_msgs"
    schema = json.loads((ROOT / "src/t1_lab/_vendor/sdk-schema.json").read_text())["t1"]
    for relative, digest in schema.items():
        path = prefix / "share/aimdk_msgs" / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit("生成接口摘要不匹配：" + relative)
    (prefix / "bridge-sdk.json").write_text(json.dumps({"family": "t1", "commit": COMMIT}) + "\n")
    print("SDK 构建与接口校验通过：" + str(prefix))
    print("下一步：python3 scripts/prepare_local.py --sdk-prefix " + str(prefix) + " --robot-ip <机器人IP>")


if __name__ == "__main__":
    main()
