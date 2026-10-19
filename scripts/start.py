#!/usr/bin/env python3
"""Start the workbench, open its page, and restart connections after config changes."""

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from t1_lab.connection import config_id, read_config, update_connection, validate_connection


def running(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=0.5) as response:
            value = json.load(response)
            return value if value.get("service") == "t1-sdk-lab" else None
    except (OSError, ValueError):
        return None


def stop_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/local.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--robot-ip", help="保存机器人 IPv4 地址，自动重新建立所有连接")
    parser.add_argument("--ros-domain-id", type=int, help="保存 ROS domain（0–232）")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    args.config = args.config.resolve()
    if not ipaddress.ip_address(args.host).is_loopback or not 1024 <= args.port <= 65535:
        parser.error("使用本机地址与 1024–65535 端口")
    if not (ROOT / ".venv/bin/python").is_file() or not args.config.is_file():
        parser.error("尚未准备 Python 环境或本地配置；请按 README 的“从零准备”完成一次性安装")
    host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{host}:{args.port}"
    existing = running(url)
    identity = config_id(args.config)
    if existing and existing.get("config_id") not in (None, identity):
        parser.error("此端口运行着另一份连接配置，请使用其他 --port")
    try:
        config, revision = update_connection(
            args.config, robot_ip=args.robot_ip, ros_domain_id=args.ros_domain_id
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    def show_page(open_browser=True):
        print(f"T1 Studio 已就绪：{url}", flush=True)
        if (
            open_browser
            and not args.no_browser
            and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        ):
            webbrowser.open(url, new=2)

    def matches(value):
        return (
            value is not None
            and value.get("config_id") == identity
            and value.get("robot_ip") == config["robot_ip"]
            and value.get("ros_domain_id") == config.get("ros_domain_id", 0)
        )

    # Hold one launcher lock across backend restarts, including the brief port gap.
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    with (runtime / f"launcher-{args.host}-{args.port}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有启动器运行，正在等待当前连接配置生效…", flush=True)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if matches(running(url)):
                    show_page()
                    return 0
                time.sleep(0.3)
            print("连接配置已保存，服务尚未就绪，请查看原启动终端。", file=sys.stderr)
            return 1
        if existing:
            print("检测到已运行的工作台，复用现有服务。", flush=True)
            if not matches(existing):
                print("已保存连接配置；当前服务由其他入口启动，请停止后重新运行 ./start.sh。", flush=True)
            show_page()
            return 0
        if not (ROOT / "src/t1_lab/static/index.html").is_file():
            if not (ROOT / "web/node_modules").is_dir():
                subprocess.run(["npm", "--prefix", str(ROOT / "web"), "ci"], check=True)
            subprocess.run(["npm", "--prefix", str(ROOT / "web"), "run", "build"], check=True)

        stopped = False
        child = None
        opened = False

        def terminate(*_):
            nonlocal stopped
            stopped = True
            if child is not None and child.poll() is None:
                child.terminate()

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        try:
            while not stopped:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "scripts/run.py"),
                        "--config",
                        str(args.config),
                        "--host",
                        args.host,
                        "--port",
                        str(args.port),
                    ],
                    cwd=ROOT,
                    env={**os.environ, "T1_LAB_CONFIG_WATCHER": str(args.config)},
                )
                ready = False
                restart = False
                invalid_revision = None
                while child.poll() is None and not stopped:
                    if not ready and matches(running(url)):
                        show_page(open_browser=not opened)
                        ready, opened = True, True
                    try:
                        updated, next_revision = read_config(args.config)
                        if next_revision != revision:
                            validate_connection(updated.get("robot_ip"), updated.get("ros_domain_id", 0))
                            config, revision = updated, next_revision
                            print("连接配置已更新，正在重启本地连接…", flush=True)
                            restart = True
                            break
                    except (ValueError, OSError) as exc:
                        problem = str(exc)
                        if problem != invalid_revision:
                            print(f"配置无法读取，保留当前连接：{problem}", file=sys.stderr, flush=True)
                            invalid_revision = problem
                    time.sleep(0.3)
                stop_child(child)
                if not restart:
                    return 0 if stopped else child.returncode
        finally:
            stop_child(child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
