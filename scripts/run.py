#!/usr/bin/env python3
"""Start with a clean ROS environment and the project's own T1 overlay."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from t1_lab.connection import materialize_dds


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, default=ROOT / "config/local.json")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--check", action="store_true")
    checks.add_argument("--check-speech", action="store_true")
    options, remaining = parser.parse_known_args()
    config_path = options.config.resolve()
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config_root = config_path.parent.parent
    prefix = (config_root / config.get("sdk_prefix", "runtime/t1/aimdk_msgs")).resolve()
    profile = materialize_dds(config_path, config, ROOT / "runtime")
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        raise SystemExit("先执行 python3 -m venv .venv，再执行 .venv/bin/pip install -e '.[dev]'")
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
    env.update(
        PYTHONNOUSERSITE="1",
        RMW_IMPLEMENTATION="rmw_fastrtps_cpp",
        ROS_DOMAIN_ID=str(config.get("ros_domain_id", 0)),
    )
    if profile.exists():
        env["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(profile)
    # Positional arguments avoid shell interpolation of paths or configuration.
    script = """
if [ -f /opt/ros/humble/setup.bash ]; then source /opt/ros/humble/setup.bash; fi
export AMENT_PREFIX_PATH="$1:${AMENT_PREFIX_PATH:-}"
export LD_LIBRARY_PATH="$1/lib:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="$1/local/lib/python3.10/dist-packages:$1/lib/python3.10/site-packages:${PYTHONPATH:-}"
shift
exec "$@"
"""
    command = [str(python), "-m", "t1_lab", "--config", str(config_path), *remaining]
    if options.check:
        command = [str(python), str(ROOT / "scripts/check_readonly.py")]
    if options.check_speech:
        command = [str(python), str(ROOT / "scripts/check_speech.py"), "--config", str(config_path)]
    os.chdir(ROOT)
    os.execvpe("bash", ["bash", "--noprofile", "--norc", "-c", script, "t1-lab", str(prefix), *command], env)


if __name__ == "__main__":
    main()
