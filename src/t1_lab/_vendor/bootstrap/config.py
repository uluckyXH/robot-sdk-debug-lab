import ipaddress
import json
import os
from pathlib import Path
import re
from dataclasses import dataclass, field

from t1_lab._vendor.contracts.catalog import COMMANDS, COMMITS
from t1_lab._vendor.services.motion_policy import settings as motion_settings

SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")


@dataclass(frozen=True)
class Config:
    family: str
    bridge_id: str
    device_uid: str
    read_only: bool = True
    simulated: bool = False
    ros_domain_id: int = 0
    host: str = "127.0.0.1"
    port: int | None = None
    token_file: str | None = None
    journal: str = "bridge-state/commands.sqlite3"
    sdk_prefix: str | None = None
    enabled_commands: tuple = tuple(sorted(COMMANDS))
    verification_note: str = ""
    scale_verified: bool = False
    audio: dict = field(default_factory=dict)
    stale_after: float = 5.0
    motion: dict = field(default_factory=dict)

    def __post_init__(self):
        if type(self.read_only) is not bool:
            raise ValueError("read_only 须为布尔值")
        if self.family not in COMMITS:
            raise ValueError("本项目仅支持 T1")
        for value in (self.bridge_id, self.device_uid):
            if not isinstance(value, str) or not SLUG.fullmatch(value):
                raise ValueError("bridge_id/device_uid 须为最多 64 字符的字母数字标识")
        if type(self.simulated) is not bool or type(self.scale_verified) is not bool:
            raise ValueError("simulated/scale_verified 须为布尔值")
        if type(self.ros_domain_id) is not int or not 0 <= self.ros_domain_id <= 232:
            raise ValueError("ros_domain_id 须为 0–232 整数")
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("桥接仅监听回环地址；远端须通过已验证隧道或认证 TLS 代理")
        if self.port is None:
            object.__setattr__(self, "port", 9081 if self.family == "q1" else 9082)
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("port 须为 1024–65535 整数")
        if not isinstance(self.enabled_commands, (list, tuple)) or any(
            x not in COMMANDS for x in self.enabled_commands
        ):
            raise ValueError("enabled_commands 含未知命令")
        if not self.simulated and self.scale_verified and not self.verification_note.strip():
            raise ValueError("确认遥测比例须填写人工核验记录 verification_note")
        if type(self.stale_after) not in (int, float) or not 0.5 <= self.stale_after <= 60:
            raise ValueError("stale_after 须为 0.5–60 秒")
        if not isinstance(self.audio, dict):
            raise ValueError("audio 须为对象")
        if not self.journal or not isinstance(self.journal, str) or self.journal == ":memory:":
            raise ValueError("journal 须为持久文件路径")
        # T1 named defaults never dispatch on startup; explicit disablement is retained.
        object.__setattr__(self, "motion", motion_settings(self.family, self.motion))
        runtime = self.motion["runtime"]
        if runtime and (self.family != "t1" or runtime.get("device_uid", self.device_uid) != self.device_uid):
            raise ValueError("运动核验配置须属于当前 T1 device_uid")


def load_config(path):
    path = Path(path)
    if path.stat().st_size > 16384:
        raise ValueError("配置过大")
    return Config(**json.loads(path.read_text()))


def load_token(config):
    value = os.environ.get("ROBOWEAVE_BRIDGE_TOKEN")
    if value is None and config.token_file:
        path = Path(config.token_file)
        stat = path.stat()
        if stat.st_mode & 0o077 or stat.st_size > 512 or not path.is_file():
            raise ValueError("token 文件须为仅所有者可读写的小文件（chmod 600）")
        value = path.read_text().strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", value):
        raise ValueError("通过 ROBOWEAVE_BRIDGE_TOKEN 或 token_file 设置 32–256 字符 Bearer 密钥")
    return value
