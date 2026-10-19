#!/usr/bin/env python3
"""Read-only live acceptance. No command, motion, heartbeat or stop API is called."""

import asyncio
import json
import math
from array import array
from datetime import datetime, timezone
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8090"


async def main():
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "control_requests_sent": 0}
    async with httpx.AsyncClient(base_url=URL, timeout=25, trust_env=False) as client:
        health = (await client.get("/api/health")).raise_for_status().json()
        if health["read_only"] is not True:
            raise SystemExit("此验收入口仅用于只读模式，已退出。")
        diagnostics = (await client.get("/api/diagnostics")).raise_for_status().json()
        assert not diagnostics["write_clients"] and not diagnostics["velocity_publisher_created"]
        report["native_guards"] = diagnostics
        state = (await client.get("/api/state")).raise_for_status().json()
        report["connected"] = state["connected"]
        report["telemetry"] = {
            group: {
                "fresh": [field["key"] for field in fields if field["availability"] == "fresh"],
                "missing": [field["key"] for field in fields if field["availability"] != "fresh"],
            }
            for group, fields in state["telemetry"].items()
        }
        report["captures"] = []
        for source in ("sdk", "video"):
            for camera in ("left", "right"):
                response = await client.post("/api/snapshots", json={"source": source, "camera": camera})
                result = response.json()
                report["captures"].append(
                    {
                        "source": source,
                        "camera": camera,
                        "http_status": response.status_code,
                        "result": result,
                    }
                )
        try:
            async with connect(URL.replace("http", "ws") + "/api/audio", origin=URL) as socket:
                first = json.loads(await asyncio.wait_for(socket.recv(), 8))
                if first.get("type") != "format":
                    raise ValueError(first)
                raw = bytearray()
                while len(raw) < 64000:
                    chunk = await asyncio.wait_for(socket.recv(), 5)
                    if not isinstance(chunk, bytes):
                        raise ValueError(chunk)
                    raw.extend(chunk)
                pcm = array("h", raw)
                report["audio"] = {
                    "format": first,
                    "bytes": len(raw),
                    "samples": len(pcm),
                    "peak": max(abs(v) for v in pcm),
                    "rms": round(math.sqrt(sum(v * v for v in pcm) / len(pcm)), 2),
                    "nonzero_samples": sum(v != 0 for v in pcm),
                    "persisted": False,
                }
        except Exception as exc:
            report["audio"] = {"error": str(exc)}
        for _ in range(30):
            state = (await client.get("/api/state")).raise_for_status().json()
            if not state["audio"]["active"] and not any(
                item["subscribers"] for item in state["video"].values()
            ):
                break
            await asyncio.sleep(0.1)
        report["cleanup"] = {"audio_active": state["audio"]["active"], "video": state["video"]}
    path = ROOT / "logs/live-readonly.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    okay = (
        report["connected"]
        and all(item["http_status"] == 200 for item in report["captures"])
        and report["audio"].get("bytes", 0) >= 64000
        and not report["cleanup"]["audio_active"]
    )
    raise SystemExit(0 if okay else 1)


if __name__ == "__main__":
    asyncio.run(main())
