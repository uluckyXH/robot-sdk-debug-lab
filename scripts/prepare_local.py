#!/usr/bin/env python3
"""Copy a compiled T1 overlay and local connection settings. Never connects to hardware."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from t1_lab.connection import bind_media_to_robot, dds_template

SDK_COMMIT = "fe2e186c867391ec78b301accdc91f0260ad0ff3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-roboweave", type=Path)
    parser.add_argument("--sdk-prefix", type=Path)
    parser.add_argument("--robot-ip")
    parser.add_argument("--domain", type=int)
    args = parser.parse_args()
    existing = ROOT / "config/local.json"
    if existing.exists():
        parser.error("config/local.json 已存在；为保留本地设置，不覆盖，请直接编辑或另存后再准备")
    source_config = {}
    if args.from_roboweave:
        candidates = []
        for path in (args.from_roboweave / "data/robot-sdk").glob("*/config.json"):
            value = json.loads(path.read_text())
            if value.get("family") == "t1":
                candidates.append((bool(value.get("audio")), path.stat().st_mtime, value))
        if not candidates:
            parser.error("源目录没有已配置的 T1")
        source_config = max(candidates, key=lambda item: item[:2])[2]
    prefix = args.sdk_prefix or Path(source_config.get("sdk_prefix", "runtime/missing"))
    record_path = prefix / "bridge-sdk.json"
    if not record_path.is_file() or json.loads(record_path.read_text()) != {
        "family": "t1",
        "commit": SDK_COMMIT,
    }:
        parser.error("需要固定版本 T1 aimdk_msgs 前缀及 bridge-sdk.json 构建记录")
    schema = json.loads((ROOT / "src/t1_lab/_vendor/sdk-schema.json").read_text())["t1"]

    def complete_sdk(candidate):
        return all(
            (candidate / "share/aimdk_msgs" / relative).is_file()
            and hashlib.sha256((candidate / "share/aimdk_msgs" / relative).read_bytes()).hexdigest() == digest
            for relative, digest in schema.items()
        )

    if not complete_sdk(prefix):
        source = prefix.parent.parent / "vendor-sdk"
        if not source.is_dir() and args.from_roboweave:
            candidates = list((args.from_roboweave / "runtimes/robot-sdk/native/t1").glob("*/vendor-sdk"))
            source = next(
                (p for p in candidates if (p / "aimdk_msgs/hal/srv/SetServo.srv").is_file()), source
            )
        if not source.is_dir():
            parser.error("消息包尚不完整；先执行 build_sdk.py 编译全部接口及厂家文档补充消息")
        workspace = ROOT / "runtime/sdk-prepared"
        prepared = workspace / "install/aimdk_msgs"
        if not complete_sdk(prepared):
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/build_sdk.py"),
                    "--source",
                    str(source),
                    "--output",
                    str(workspace),
                ],
                check=True,
            )
        prefix = prepared
    destination = ROOT / "runtime/t1/aimdk_msgs"
    if destination.exists():
        parser.error("runtime/t1/aimdk_msgs 已存在，不覆盖已有接口")
    # Dereference build symlinks: deleting RoboWeave must not break this project.
    shutil.copytree(prefix, destination, symlinks=False, ignore=shutil.ignore_patterns("__pycache__"))
    audio = source_config.get("audio", {})
    address = args.robot_ip
    if not address and audio.get("server", "").startswith("tcp:"):
        address = audio["server"].split(":")[1]
    address = address or "192.0.2.1"
    import ipaddress

    ipaddress.IPv4Address(address)
    if args.robot_ip and audio.get("transport") == "tcp":
        audio = {**audio, "server": f"tcp:{address}:{audio['server'].rsplit(':', 1)[1]}"}
    if audio.get("cookie_file"):
        cookie = ROOT / "runtime/pulse.cookie"
        shutil.copyfile(audio["cookie_file"], cookie)
        cookie.chmod(0o600)
        audio = {**audio, "cookie_file": str(cookie)}
    value = {
        "name": "T1 · 开发机器人",
        "robot_ip": address,
        "ros_domain_id": args.domain if args.domain is not None else source_config.get("ros_domain_id", 0),
        "read_only": False,
        "sdk_prefix": "runtime/t1/aimdk_msgs",
        "dds_profile": "config/fastdds.xml",
        "data_dir": "data",
        "rtsp_port": 2554,
        "video_fps": 15,
        "video_width": 1280,
        "audio": audio,
        "motion": {},
    }
    existing.parent.mkdir(exist_ok=True)
    existing.write_text(json.dumps(bind_media_to_robot(value), ensure_ascii=False, indent=2) + "\n")
    existing.chmod(0o600)
    (ROOT / "config/fastdds.xml").write_text(dds_template())
    print("已复制独立 T1 接口并写入本地配置，页面控制可用；未连接或控制机器人。")


if __name__ == "__main__":
    main()
