#!/usr/bin/env python3
"""Generate the complete T1 schema from pinned local sources. No ROS initialization."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "fe2e186c867391ec78b301accdc91f0260ad0ff3"
SERVICES = [
    ("GetMcAction", "运动模式查询", "运动", False),
    ("SetMcAction", "运动模式设置", "运动", True),
    ("SetMcMotion", "全身动作与模型", "运动", True),
    ("SetMcPresetMotion", "预设动作", "运动", True),
    ("SetMcInputSource", "控制输入源设置", "运动", True),
    ("GetCurrentInputSource", "控制输入源查询", "运动", False),
    ("SetServo", "形态舵机", "关节", True),
    ("PlayTts", "语音播报", "音频", True),
    ("GetVolume", "查询音量", "音频", False),
    ("SetVolume", "设置音量", "音频", True),
    ("GetMute", "查询静音", "音频", False),
    ("SetMute", "设置静音", "音频", True),
    ("PlayAudioFile", "机器人本地音频文件", "音频", True),
    ("PlayEmotion", "表情与屏幕视频", "屏幕", True),
    ("ShutdownTask", "机器人关机", "系统", True),
    ("CaptureJpegImage", "相机单帧", "视觉", False),
    ("LedStripCommand", "灯带样式与颜色", "灯光", True),
    ("SetNeckLight", "颈部灯光", "灯光", True),
]
TOPICS = [
    ("JointCommandArray", "全身底层关节", "关节", "/aima/hal/joint/command", True, "best_effort", 500),
    (
        "McCustomJointCommand",
        "自定义上肢关节",
        "关节",
        "/aima/mc/custom/joint/command",
        True,
        "best_effort",
        100,
    ),
    ("JointStateArray", "关节状态反馈", "关节", "/aima/hal/joint/state", False, "best_effort", 0),
    ("McLocomotionVelocity", "原始速度指令", "运动", "/aima/mc/locomotion/velocity", True, "reliable", 50),
    ("AudioCapture", "ROS 音频采集", "音频", "/aima/hal/audio/capture", False, "reliable", 0),
    ("AudioPlayback", "ROS 音频回放", "音频", "/aima/hal/audio/playback", True, "reliable", 0),
    (
        "AudioDeviceInfoList",
        "音频设备列表",
        "音频",
        "/aima/hal/audio/audio_device_list",
        False,
        "best_effort",
        0,
    ),
    ("TouchState", "触摸状态", "传感器", "/aima/hal/touch/state", False, "best_effort", 0),
    ("Bms", "电池状态", "传感器", "/aima/hal/bms/state", False, "best_effort", 0),
    ("NeckLightState", "颈部灯光状态", "灯光", "/aima/hal/neck_light/state", False, "best_effort", 0),
]


def definition(text):
    fields, constants = [], {}
    for raw in text.splitlines():
        line, _, comment = raw.partition("#")
        line = line.strip()
        if not line:
            continue
        type_name, rest = line.split(None, 1)
        if "=" in rest:
            name, value = rest.split("=", 1)
            try:
                constants[name.strip()] = int(value.strip(), 0)
            except ValueError:
                constants[name.strip()] = value.strip().strip("\"'")
        else:
            fields.append({"name": rest.strip(), "type": type_name, "description": comment.strip()})
    return {"fields": fields, "constants": constants}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() != COMMIT:
        parser.error("需要固定版本 " + COMMIT)
    documentation = (source / "接口说明.md").read_text()
    files = {
        f"{p.suffix[1:]}/{p.name}": p
        for p in (source / "aimdk_msgs").rglob("*")
        if p.suffix in (".msg", ".srv")
    }
    supplemental = {}
    for name in ("AudioCapture", "AudioPlayback"):
        if "msg/" + name + ".msg" not in files:
            match = re.search(
                r"<!-- PROTOCOL_START: " + name + r" -->.*?```msg\n(.*?)```", documentation, re.S
            )
            if not match:
                raise ValueError("厂家文档未包含 " + name)
            path = ROOT / "vendor_additions/aimdk_msgs/msg" / (name + ".msg")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(match.group(1))
            files["msg/" + path.name] = path
            supplemental[path.name] = {
                "source": "接口说明.md#PROTOCOL_START:" + name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    schema, hashes = {}, {}
    for relative, path in sorted(files.items()):
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix == ".srv":
            request, response = path.read_text().split("\n---", 1)
            schema[path.stem + ".Request"] = definition(request)
            schema[path.stem + ".Response"] = definition(response)
        else:
            schema[path.stem] = definition(path.read_text())
    schema["builtin_interfaces/Time"] = {
        "fields": [{"name": "sec", "type": "int32"}, {"name": "nanosec", "type": "uint32"}],
        "constants": {},
    }
    entries = [
        {
            "id": name,
            "label": label,
            "category": category,
            "kind": "service",
            "write": write,
            "path": "/aima/hal/camera/CaptureJpegImage"
            if name == "CaptureJpegImage"
            else "/aimdk_5Fmsgs/srv/" + name,
            "type": "aimdk_msgs/srv/" + name,
            "schema": name + ".Request",
            "source": "vendor-source",
        }
        for name, label, category, write in SERVICES
    ]
    entries += [
        {
            "id": name,
            "label": label,
            "category": category,
            "kind": "publish" if write else "subscribe",
            "write": write,
            "path": path,
            "type": "aimdk_msgs/msg/" + name,
            "schema": name,
            "qos": qos,
            "rate_hz": rate,
            "source": "vendor-documentation-supplement" if name + ".msg" in supplemental else "vendor-source",
        }
        for name, label, category, path, write, qos, rate in TOPICS
    ]
    for entry in entries:
        if entry["id"] == "NeckLightState":
            entry["aliases"] = ["/aimdk_5Fmsgs/msg/NeckLightState"]
            entry["note"] = "厂家目录与详情给出不同话题名，订阅时同时兼容两者。"
    entries += [
        {
            "id": "RTSP",
            "label": "双目视频",
            "category": "视觉",
            "kind": "transport",
            "write": False,
            "path": "rtsp://<robot>:2554/live_<camera_id>",
            "type": "RTSP",
            "source": "vendor-documentation",
        },
        {
            "id": "PulseCapture",
            "label": "TCP 麦克风监听",
            "category": "音频",
            "kind": "transport",
            "write": False,
            "path": "tcp://<robot>:4713",
            "type": "PulseAudio native",
            "source": "vendor-documentation",
        },
        {
            "id": "TcpAudioPlayback",
            "label": "上传音频到扬声器",
            "category": "音频",
            "kind": "transport",
            "write": True,
            "path": "tcp://<robot>:6001",
            "type": "PCM s16le / 48000 Hz / mono",
            "source": "vendor-documentation",
        },
    ]
    joints = []
    limits = re.search(
        r"<!-- DERIVED_TABLE_START: JointLimitConfig -->(.*?)<!-- DERIVED_TABLE_END", documentation, re.S
    ).group(1)
    for name, low, high, effort in re.findall(
        r"`([^`]+_Joint)`\s*\|\s*([\d.-]+)\s*\|\s*([\d.-]+)\s*\|\s*([\d.-]+)", limits
    ):
        joints.append(
            {
                "name": name,
                "minimum": float(low),
                "maximum": float(high),
                "max_effort": float(effort),
                "upper": name.startswith(("FL_", "FR_")),
            }
        )
    if len(joints) != 14 or set(n for n, *_ in SERVICES) != {
        Path(k).stem for k in files if k.startswith("srv/")
    }:
        raise ValueError("服务或关节覆盖不完整")
    catalog = {
        "sdk_commit": COMMIT,
        "entries": entries,
        "definitions": schema,
        "joints": joints,
        "supplemental": supplemental,
    }
    (ROOT / "src/t1_lab/sdk/interface_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    )
    (ROOT / "src/t1_lab/_vendor/sdk-schema.json").write_text(json.dumps({"t1": hashes}, indent=2) + "\n")
    print(f"已生成 {len(entries)} 个接口、{len(hashes)} 个消息/服务文件、{len(joints)} 个关节；未加载 ROS。")


if __name__ == "__main__":
    main()
