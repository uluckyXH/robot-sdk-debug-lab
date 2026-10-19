import base64
import uuid

from t1_lab._vendor.adapters.audio import PulseAudio
from t1_lab._vendor.contracts.catalog import BridgeError, COMMITS, command_arguments, now
from t1_lab._vendor.services.commands import Commands
from t1_lab._vendor.services.motion_runtime import MotionRuntime
from t1_lab._vendor.services.motion_runtime_policy import ACTIONS
from t1_lab._vendor.storage.ledger import Ledger
from .config import CAMERAS, Settings

READ_ONLY_REASON = "只读模式已启用；修改 config/local.json 的 read_only 并重启后才能操作机器人"


class T1Client:
    """Reusable SDK. All writes share one guard and the native adapter adds a second guard."""

    def __init__(self, settings: Settings, *, adapter=None):
        self.settings = settings
        self.config = settings.native()
        self.adapter = adapter
        self.audio = PulseAudio(settings.audio)
        self.error = None
        self.ledger = None
        self.commands = None
        self.motion_runtime = None
        self.scope = None
        from .interfaces import InterfaceGateway
        from .playback import TcpAudioPlayer

        self.interfaces = InterfaceGateway(self)
        self.audio_player = TcpAudioPlayer(self)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self):
        # Acquire the instance journal before creating ROS clients. A concurrent
        # startup must fail without leaving a native executor running.
        self.ledger = Ledger(self.config)
        try:
            if self.adapter is None:
                from t1_lab._vendor.adapters.ros.adapter import RosAdapter

                self.adapter = RosAdapter(self.config)
            await self.adapter.start()
            self.audio = self.adapter.audio
        except Exception as exc:
            self.error = str(exc)
            if self.adapter is not None:
                await self.adapter.close()
            self.adapter = None
        if self.adapter is not None:
            self.commands = Commands(self.adapter, self.ledger)
            self.motion_runtime = MotionRuntime(self.config, self.adapter, self.ledger)

    def require_write(self):
        if self.settings.read_only:
            raise BridgeError(403, READ_ONLY_REASON)
        if self.adapter is None:
            raise BridgeError(503, self.error or "原生 SDK 未连接")

    def state(self):
        evidence = self.adapter.evidence() if self.adapter else {"source_verified": False}
        return {
            "name": self.settings.name,
            "family": "T1",
            "robot_ip": self.settings.robot_ip,
            "ros_domain_id": self.settings.ros_domain_id,
            "read_only": self.settings.read_only,
            "sdk_ready": self.adapter is not None,
            "connected": evidence.get("source_verified") is True,
            "error": self.error,
            "observed_at": now(),
            "sdk_commit": COMMITS["t1"],
            "command_epoch": self.ledger.epoch if self.ledger else None,
            "evidence": evidence,
            "telemetry": self.adapter.state() if self.adapter else {},
            "audio": {
                "configured": self.audio.available,
                "active": self.audio.busy,
                "sample_rate": 16000,
                "channels": 1,
                "format": "s16le",
                "channel": self.settings.audio.get("channel"),
                "device": self.settings.audio.get("device"),
            },
        }

    def capabilities(self):
        capabilities = self.adapter.capabilities() if self.adapter else []
        if self.settings.read_only or self.motion_runtime is None:
            reason = READ_ONLY_REASON if self.settings.read_only else "原生 SDK 未连接"
            motion = {
                "actions": [
                    {
                        "action": name,
                        "label": spec["label"],
                        "kind": spec["kind"],
                        "available": False,
                        "reason": reason,
                    }
                    for name, spec in ACTIONS.items()
                ],
                "current": None,
                "stop": {"available": False, "reason": reason},
            }
        else:
            motion = self.motion_runtime.info()
        return {"read_only": self.settings.read_only, "interfaces": capabilities, "motion": motion}

    def diagnostics(self):
        from t1_lab._vendor.contracts.catalog import READ_SERVICES

        names = sorted(getattr(self.adapter, "clients", {}))
        return {
            "read_only": self.settings.read_only,
            "sdk_ready": self.adapter is not None,
            "service_clients": names,
            "write_clients": [name for name in names if name not in READ_SERVICES and name != "frame"],
            "velocity_publisher_created": getattr(self.adapter, "velocity_publisher", None) is not None,
            "sdk_prefix": self.config.sdk_prefix,
            "error": self.error,
        }

    async def frame(self, camera="left"):
        if camera not in CAMERAS:
            raise BridgeError(422, "camera 须为 left 或 right")
        if self.adapter is None:
            raise BridgeError(503, self.error or "原生 SDK 未连接")
        result = await self.adapter.frame(CAMERAS[camera])
        return base64.b64decode(result["image"].split(",", 1)[1], validate=True), result

    async def command(self, kind, arguments, command_id):
        self.require_write()
        command_arguments("t1", kind, arguments)
        if kind not in self.config.enabled_commands:
            raise BridgeError(403, "此命令未在 enabled_commands 中启用")
        return await self.commands.submit(
            {
                "device_uid": self.config.device_uid,
                "command_id": command_id,
                "kind": kind,
                "arguments": arguments,
            }
        )

    def _motion_body(self, command_id):
        if not self.config.motion["enabled"]:
            raise BridgeError(403, "motion.enabled=false：运动控制已关闭")
        if self.scope is None:
            previous = self.ledger.motion_fence or {}
            self.scope = {
                "device_uid": self.config.device_uid,
                "binding_revision": previous.get("binding_revision", 0) + 1,
                "connection_epoch": self.ledger.epoch,
                "control_revision": previous.get("control_revision", 0) + 1,
            }
            self.motion_runtime.bind(self.scope)
        return {**self.scope, "command_id": command_id, "source": "manual"}

    async def move(self, action, command_id):
        self.require_write()
        if self.interfaces.stream is not None:
            raise BridgeError(409, "SDK 关节／速度发布流正在运行，请先结束当前流")
        if self.interfaces.operation_lock.locked():
            raise BridgeError(409, "SDK 调用正在处理，请待回执返回后操作")
        if action not in ACTIONS:
            raise BridgeError(422, "未知 T1 动作")
        return await self.motion_runtime.request({**self._motion_body(command_id), "action": action})

    def heartbeat(self, command_id):
        self.require_write()
        return self.motion_runtime.heartbeat(self._motion_body(command_id))

    async def stop(self, command_id):
        self.require_write()
        return await self.motion_runtime.stop(self._motion_body(command_id))

    def receipt(self, command_id):
        return self.ledger.get(command_id)

    def new_command_id(self):
        return self.ledger.epoch + ":" + uuid.uuid4().hex

    async def call_interface(self, name, arguments, command_id=None):
        """Call any of the 18 pinned vendor services with its original request fields."""
        return await self.interfaces.call(name, arguments, command_id or self.new_command_id())

    async def topic(self, name):
        return await self.interfaces.topic(name)

    async def messages(self, name):
        """Stream topic samples; a bounded queue drops old frames for slow consumers."""
        async with self.interfaces.watch(name) as queue:
            while True:
                sample = await queue.get()
                if sample is None:
                    return
                yield sample

    async def set_servo(self, position, speed=100, command_id=None):
        return await self.call_interface("SetServo", {"position": position, "speed": speed}, command_id)

    async def publish_interface(self, name, arguments, *, rate_hz=1, duration_ms=1000, command_id=None):
        return await self.interfaces.publish(
            name, arguments, command_id or self.new_command_id(), rate_hz=rate_hz, duration_ms=duration_ms
        )

    async def control_joints(self, joints, *, channel="hal", duration_ms=1000, rate_hz=None, command_id=None):
        if channel not in ("hal", "upper"):
            raise BridgeError(422, "channel 须为 hal 或 upper")
        name = "JointCommandArray" if channel == "hal" else "McCustomJointCommand"
        return await self.publish_interface(
            name,
            {"joints": joints},
            rate_hz=rate_hz or (500 if channel == "hal" else 100),
            duration_ms=duration_ms,
            command_id=command_id,
        )

    def stream_heartbeat(self, command_id):
        return self.interfaces.heartbeat(command_id)

    async def stop_stream(self, command_id):
        return await self.interfaces.stop(command_id)

    async def play_audio(self, data, format="wav", command_id=None):
        return await self.audio_player.start(data, format, command_id or self.new_command_id())

    async def close(self):
        await self.audio_player.close()
        await self.interfaces.close()
        if self.motion_runtime is not None:
            await self.motion_runtime.close()
        if self.commands is not None:
            await self.commands.close()
        await self.audio.close()
        if self.adapter is not None:
            await self.adapter.close()
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None
