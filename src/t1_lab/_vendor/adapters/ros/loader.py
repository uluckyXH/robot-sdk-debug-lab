"""Check the loaded message package and installed interfaces, without vendoring SDK code."""

import hashlib
import json
import os
from pathlib import Path
from t1_lab._vendor.resources import resource_file
import platform
import sys

from t1_lab._vendor.families import COMMITS, FAMILIES

RUNTIMES = {
    "humble": {"ubuntu": "22.04", "python": (3, 10), "support": "vendor-baseline"},
    "jazzy": {"ubuntu": "24.04", "python": (3, 12), "support": "project-source-rebuild"},
}


def runtime_info():
    """Validate the OS/ABI combination without importing ROS or initializing DDS."""
    if platform.system() != "Linux":
        raise ValueError("原生 SDK 需要 Ubuntu Linux/WSL2；Mac 测试不能代替 Linux ROS 自检")
    distribution = platform.freedesktop_os_release()
    distro = os.environ.get("ROS_DISTRO", "")
    expected = RUNTIMES.get(distro)
    if expected is None:
        raise ValueError("先加载匹配的 ROS 环境：Ubuntu 22.04/Humble 或 Ubuntu 24.04/Jazzy")
    if distribution.get("ID") != "ubuntu" or distribution.get("VERSION_ID") != expected["ubuntu"]:
        raise ValueError("ROS/Ubuntu 组合不匹配；仅支持 22.04/Humble 与 24.04/Jazzy")
    if tuple(sys.version_info[:2]) != expected["python"] or sys.implementation.name != "cpython":
        raise ValueError(
            "ROS/Python ABI 不匹配：" + distro + " 要求 CPython " + ".".join(map(str, expected["python"]))
        )
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine())
    if not arch:
        raise ValueError("原生 SDK 只支持 ARM64 或 x64")
    if os.environ.get("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp") != "rmw_fastrtps_cpp":
        raise ValueError("当前固定 SDK 只使用 rmw_fastrtps_cpp；请清理其他 RMW 环境")
    return {
        "ros_distro": distro,
        "python_version": ".".join(map(str, expected["python"])),
        "ubuntu": expected["ubuntu"],
        "architecture": arch,
        "support": expected["support"],
        "ros_prefix": "/opt/ros/" + distro,
        "hardware_validation": "pending",
    }


def verify_environment(prefix, distro):
    """Reject another same-named SDK or ROS distribution even when ours currently wins lookup."""
    prefix = Path(prefix).resolve()
    for variable in (
        "AMENT_PREFIX_PATH",
        "CMAKE_PREFIX_PATH",
        "COLCON_PREFIX_PATH",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
    ):
        for raw in os.environ.get(variable, "").split(os.pathsep):
            if not raw:
                continue
            path = Path(raw).resolve()
            parts = path.parts
            for index in range(len(parts) - 2):
                if parts[index : index + 2] == ("opt", "ros") and parts[index + 2] != distro:
                    raise ValueError(variable + " 混入其他 ROS 发行版；请在干净终端重新加载环境")
            if variable in ("AMENT_PREFIX_PATH", "CMAKE_PREFIX_PATH", "COLCON_PREFIX_PATH"):
                marker = path / "share/ament_index/resource_index/packages/aimdk_msgs"
                if marker.is_file() and path != prefix:
                    raise ValueError(variable + " 混入其他 aimdk_msgs 工作区；Q1/T1 必须分进程隔离")
            if variable == "LD_LIBRARY_PATH" and path.is_dir():
                if any(path.glob("libaimdk_msgs__*.so")) and not path.is_relative_to(prefix):
                    raise ValueError("LD_LIBRARY_PATH 混入其他型号的 aimdk_msgs 动态库")
    for raw in sys.path:
        path = Path(raw or os.getcwd()).resolve() / "aimdk_msgs"
        if path.exists() and not path.resolve().is_relative_to(prefix):
            raise ValueError("Python 路径存在其他 aimdk_msgs 包，不能混用 Q1/T1")
    for name, module in tuple(sys.modules.items()):
        if name == "aimdk_msgs" or name.startswith("aimdk_msgs."):
            location = getattr(module, "__file__", None)
            if location and not Path(location).resolve().is_relative_to(prefix):
                raise ValueError("当前 Python 已载入其他 aimdk_msgs；请使用新的隔离进程")


def verify_schema(family, package_share):
    expected = json.loads(resource_file("sdk-schema.json").read_text())[family]
    for relative, digest in expected.items():
        path = Path(package_share) / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("SDK 接口校验失败：" + relative)


def load_ros(config):
    runtime = runtime_info()
    if config.family not in COMMITS:
        raise ValueError("family 必须为 q1 或 t1")
    if not config.sdk_prefix:
        raise ValueError("必须显式设置当前型号的 sdk_prefix")
    prefix = Path(config.sdk_prefix).resolve()
    verify_environment(prefix, runtime["ros_distro"])
    import aimdk_msgs
    import rclpy
    from ament_index_python.packages import get_package_prefix, get_package_share_directory

    actual = Path(get_package_prefix("aimdk_msgs")).resolve()
    if actual != prefix or not Path(aimdk_msgs.__file__).resolve().is_relative_to(prefix):
        raise ValueError("aimdk_msgs 来自其他环境；Q1/T1 不得混装")
    ros_prefix = Path(runtime["ros_prefix"]).resolve()
    if Path(get_package_prefix("rclpy")).resolve() != ros_prefix or not Path(
        rclpy.__file__
    ).resolve().is_relative_to(ros_prefix):
        raise ValueError("rclpy 来自其他 ROS 环境；不要把旧发行版 Python/动态库复用到新发行版")
    record = json.loads((prefix / "bridge-sdk.json").read_text())
    if record != {"family": config.family, "commit": COMMITS[config.family]}:
        raise ValueError("SDK 构建记录与当前型号固定版本不符")
    share = Path(get_package_share_directory("aimdk_msgs")).resolve()
    if share != prefix / "share/aimdk_msgs":
        raise ValueError("aimdk_msgs 接口目录与当前隔离 SDK 前缀不一致")
    verify_schema(config.family, share)
    from aimdk_msgs import msg, srv

    expected = FAMILIES[config.family].JOINT_TEMPERATURE_TYPE
    fields = msg.JointState.get_fields_and_field_types()
    if fields.get("coil_temp") != expected or fields.get("motor_temp") != expected:
        raise ValueError("JointState 温度类型与当前型号不符")
    verify_environment(prefix, runtime["ros_distro"])
    return msg, srv
