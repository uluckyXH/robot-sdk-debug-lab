#!/usr/bin/env python3
"""One explicitly requested TTS test. No movement, expression, or volume writes."""

import argparse
import asyncio
from array import array
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from t1_lab.sdk import T1Client, load_settings

ROOT = Path(__file__).resolve().parent.parent
TEST_TEXT = "你好，机器人语音播报测试。"


def audio_stats(pcm):
    return {
        "samples": len(pcm),
        "peak": max((abs(value) for value in pcm), default=0),
        "rms": round(math.sqrt(sum(value * value for value in pcm) / max(1, len(pcm))), 2),
    }


async def check(config_path):
    original = load_settings(config_path)
    if original.read_only is not True:
        raise SystemExit("此入口要求工作台配置保持 read_only=true；只在本次独立进程开放播报。")
    settings = replace(
        original,
        read_only=False,
        enabled_commands=("speak",),
        enabled_interfaces=(),
        motion={"enabled": False},
        data_dir=str(ROOT / "data/speech-check"),
    )
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text": TEST_TEXT,
        "speech_requests": 0,
        "motion_requests": 0,
        "other_write_requests": 0,
        "workbench_config_read_only": True,
    }
    robot = T1Client(settings)
    collector = None
    try:
        async with robot:
            report["native_guards"] = robot.diagnostics()
            if robot.adapter is None:
                raise RuntimeError(robot.error)
            if (
                report["native_guards"]["write_clients"] != ["speak"]
                or report["native_guards"]["velocity_publisher_created"]
            ):
                raise RuntimeError("播报专用接口隔离失败，未发送请求")
            deadline = time.monotonic() + 12
            while not any(
                entry["name"] == "speak" and entry["available"]
                for entry in robot.capabilities()["interfaces"]
            ):
                if time.monotonic() >= deadline:
                    raise RuntimeError("未发现唯一 TTS 服务，未发送请求")
                await asyncio.sleep(0.2)
            report["telemetry_before"] = robot.state()["telemetry"].get("audio", [])
            capture = await robot.audio.open({"seconds": 16})
            baseline_ready = asyncio.Event()
            pcm = array("h")

            async def collect():
                async for chunk in capture.chunks():
                    pcm.frombytes(chunk)
                    if len(pcm) >= 32000:
                        baseline_ready.set()

            collector = asyncio.create_task(collect())
            await asyncio.wait_for(baseline_ready.wait(), 7)
            report["speech_requests"] = 1
            report["receipt"] = await robot.command("speak", {"text": TEST_TEXT}, robot.new_command_id())
            await asyncio.wait_for(collector, 20)
            report["audio"] = {
                "sample_rate": 16000,
                "channels": 1,
                "format": "s16le",
                "baseline": audio_stats(pcm[:32000]),
                "after_request": audio_stats(pcm[32000:]),
                "persisted": False,
                "meaning": "电平记录仅证明采音链路有信号，不能证明具体内容或播放完成",
            }
            report["telemetry_after"] = robot.state()["telemetry"].get("audio", [])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if collector is not None and not collector.done():
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)
        report["audio_active_after_close"] = robot.audio.busy
        path = ROOT / "logs/live-speech.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(
        not report.get("error")
        and report.get("receipt", {}).get("status") == "accepted"
        and not report["audio_active_after_close"]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/local.json")
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(check(args.config)) else 1)
