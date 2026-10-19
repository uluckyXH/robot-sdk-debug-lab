#!/usr/bin/env python3
"""Check local interface files, hashes and documentation coverage. No device I/O."""

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="可选固定厂家源码；核对文档中的全部接口路径")
    parser.add_argument("--sdk-prefix", type=Path)
    args = parser.parse_args()
    config = json.loads((ROOT / "config/local.json").read_text())
    prefix = args.sdk_prefix or ROOT / config["sdk_prefix"]
    catalog = json.loads((ROOT / "src/t1_lab/sdk/interface_catalog.json").read_text())
    hashes = json.loads((ROOT / "src/t1_lab/_vendor/sdk-schema.json").read_text())["t1"]
    services = {Path(name).stem for name in hashes if name.startswith("srv/")}
    entries = catalog["entries"]
    assert services == {entry["id"] for entry in entries if entry["kind"] == "service"}
    for name, digest in hashes.items():
        path = prefix / "share/aimdk_msgs" / name
        assert path.is_file(), str(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    for entry in entries:
        if entry["kind"] != "transport":
            assert entry["schema"] in catalog["definitions"], entry["id"]
    if args.source:
        documentation = (args.source / "接口说明.md").read_text()
        documented = set(re.findall(r"接口名称：`?(/[^`\s]+)", documentation))
        implemented = {entry["path"] for entry in entries} | {
            path for entry in entries for path in entry.get("aliases", [])
        }
        assert documented <= implemented, str(documented - implemented)
        source_services = {path.stem for path in (args.source / "aimdk_msgs").rglob("*.srv")}
        assert source_services == services
    assert len(catalog["joints"]) == 14
    assert sum(item["upper"] for item in catalog["joints"]) == 6
    report = {
        "kind": "static-files-only",
        "services": len(services),
        "topics": sum(entry["kind"] in ("subscribe", "publish") for entry in entries),
        "media_transports": sum(entry["kind"] == "transport" for entry in entries),
        "hashed_interface_files": len(hashes),
        "joint_names": len(catalog["joints"]),
        "device_requests_sent": 0,
        "ros_initialized": False,
        "sdk_prefix": str(prefix),
        "supplemental": catalog["supplemental"],
    }
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs/full-sdk-static.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
