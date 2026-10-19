import ipaddress
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from t1_lab.connection import resolve_media

from t1_lab._vendor.bootstrap.config import Config
from t1_lab._vendor.contracts.catalog import COMMANDS

CAMERAS = {"left": "head_stereo_left", "right": "head_stereo_right"}


@dataclass(frozen=True)
class Settings:
    name: str = "T1 · 开发机器人"
    robot_ip: str = "192.0.2.1"
    ros_domain_id: int = 0
    read_only: bool = False
    enabled_commands: tuple = tuple(sorted(COMMANDS))
    enabled_interfaces: tuple | None = None
    sdk_prefix: str = "runtime/t1/aimdk_msgs"
    dds_profile: str = "config/fastdds.xml"
    data_dir: str = "data"
    rtsp_port: int = 2554
    playback_port: int = 6001
    video_fps: int = 15
    video_width: int = 1280
    rtsp_urls: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    motion: dict = field(default_factory=dict)

    def __post_init__(self):
        ipaddress.IPv4Address(self.robot_ip)
        media = resolve_media({"robot_ip": self.robot_ip, "audio": self.audio, "rtsp_urls": self.rtsp_urls})
        object.__setattr__(self, "audio", media.get("audio", {}))
        object.__setattr__(self, "rtsp_urls", media["rtsp_urls"])
        if type(self.read_only) is not bool:
            raise ValueError("read_only 必须是 true 或 false")
        if not isinstance(self.enabled_commands, (list, tuple)) or any(
            value not in COMMANDS for value in self.enabled_commands
        ):
            raise ValueError("enabled_commands 含未知命令")
        if self.enabled_interfaces is not None:
            from .catalog import ENTRIES

            if not isinstance(self.enabled_interfaces, (list, tuple)) or any(
                value not in ENTRIES for value in self.enabled_interfaces
            ):
                raise ValueError("enabled_interfaces 须为固定 SDK 接口名称列表或 null")
        for key, low, high in (
            ("ros_domain_id", 0, 232),
            ("rtsp_port", 1, 65535),
            ("playback_port", 1, 65535),
            ("video_fps", 1, 30),
            ("video_width", 320, 1920),
        ):
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} 须为 {low}–{high} 的整数")
        if set(self.rtsp_urls) - CAMERAS.keys():
            raise ValueError("rtsp_urls 只支持 left 和 right")
        for url in self.rtsp_urls.values():
            if not isinstance(url, str) or urlparse(url).scheme != "rtsp" or not urlparse(url).hostname:
                raise ValueError("视频地址须为有效 RTSP URL")

    def rtsp_url(self, camera: str) -> str:
        if camera not in CAMERAS:
            raise ValueError("camera 须为 left 或 right")
        return self.rtsp_urls.get(camera) or f"rtsp://{self.robot_ip}:{self.rtsp_port}/live_{CAMERAS[camera]}"

    def native(self) -> Config:
        return Config(
            family="t1",
            bridge_id="t1-sdk-lab",
            device_uid="t1",
            read_only=self.read_only,
            enabled_commands=self.enabled_commands,
            ros_domain_id=self.ros_domain_id,
            sdk_prefix=str(Path(self.sdk_prefix).resolve()),
            journal=str(Path(self.data_dir) / "commands.sqlite3"),
            audio=self.audio,
            motion={**self.motion, **({"enabled": False} if self.read_only else {})},
        )


def load_settings(path="config/local.json") -> Settings:
    path = Path(path).resolve()
    if not path.is_file():
        return Settings()
    data = json.loads(path.read_text())
    # Relative paths use the project root, even when the CLI is run elsewhere.
    root = path.parent.parent
    for name in ("sdk_prefix", "dds_profile", "data_dir"):
        value = Path(data.get(name, getattr(Settings(), name)))
        data[name] = str(value if value.is_absolute() else root / value)
    return Settings(**data)
