import argparse
import ipaddress

import uvicorn

from .api import create_app
from .sdk import load_settings


def main():
    parser = argparse.ArgumentParser(description="T1 SDK Lab")
    parser.add_argument("--config", default="config/local.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8090, type=int)
    args = parser.parse_args()
    if not ipaddress.ip_address(args.host).is_loopback:
        parser.error("调试服务只监听本机；远程访问请使用 SSH 端口转发")
    settings = load_settings(args.config)
    print(
        f"T1 SDK Lab → http://{args.host}:{args.port} · {'只读模式' if settings.read_only else '控制已启用'}",
        flush=True,
    )
    uvicorn.run(
        create_app(settings, config_path=args.config),
        host=args.host,
        port=args.port,
        ws_max_size=1024,
        timeout_graceful_shutdown=5,
        access_log=False,
    )


if __name__ == "__main__":
    main()
