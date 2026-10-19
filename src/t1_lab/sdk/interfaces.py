"""Explicit full SDK operations. No writes or active probes happen during construction."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import threading
import time

from t1_lab._vendor.adapters.ros.service_discovery import inspect_service_sources
from t1_lab._vendor.contracts.catalog import BridgeError, now
from . import catalog
from .codec import plain, populate, stamp_headers

LEGACY_COMMANDS = {
    "PlayTts": "speak",
    "PlayEmotion": "expression",
    "SetVolume": "set_volume",
    "SetMute": "set_mute",
}


@dataclass
class TopicStream:
    command_id: str
    interface: str
    rate_hz: int
    duration_ms: int
    expires_at: float
    stop: threading.Event = field(default_factory=threading.Event)
    sent: int = 0
    cleanup_sent: bool = False
    task: asyncio.Task | None = None


class InterfaceGateway:
    def __init__(self, sdk):
        self.sdk = sdk
        self.clients = {}
        self.publishers = {}
        self.subscriptions = {}
        self.samples = {}
        self.watchers = {}
        self.sample_lock = threading.RLock()
        self.stream = None
        self.tasks = set()
        self.operation_lock = asyncio.Lock()
        self.closing = False

    @property
    def adapter(self):
        if self.sdk.adapter is None:
            raise BridgeError(503, self.sdk.error or "原生 SDK 未连接")
        return self.sdk.adapter

    def guard(self, name):
        if self.closing:
            raise BridgeError(409, "SDK 正在退出")
        spec = catalog.entry(name)
        if spec["write"]:
            self.sdk.require_write()
            allowed = self.sdk.settings.enabled_interfaces
            if allowed is not None and name not in allowed:
                raise BridgeError(403, "此接口已在 enabled_interfaces 中关闭")
            if name in LEGACY_COMMANDS and LEGACY_COMMANDS[name] not in self.sdk.config.enabled_commands:
                raise BridgeError(403, "此命令已在 enabled_commands 中关闭")
            if name in catalog.MOTION_INTERFACES:
                if not self.sdk.config.motion["enabled"]:
                    raise BridgeError(403, "运动控制已在 motion.enabled 中关闭")
                if self.sdk.motion_runtime and self.sdk.motion_runtime.active:
                    raise BridgeError(409, "已有常规动作正在执行，请等本次动作结束")
        return spec

    def describe(self):
        result = catalog.describe()
        for item in result["entries"]:
            module = getattr(self.sdk.adapter, "srv" if item["kind"] == "service" else "msg", None)
            item["native_type_available"] = item["kind"] == "transport" or (
                module is not None and hasattr(module, item["id"])
            )
        return {**result, "read_only": self.sdk.settings.read_only, "stream": self.stream_status()}

    async def native(self, callback, timeout=3):
        adapter = self.adapter
        loop = asyncio.get_running_loop()
        result = loop.create_future()

        def complete(value=None, error=None):
            if not result.done():
                result.set_exception(error) if error else result.set_result(value)

        def run():
            if result.done():
                return
            try:
                value = callback()
                loop.call_soon_threadsafe(complete, value)
            except Exception as exc:
                loop.call_soon_threadsafe(complete, None, exc)

        adapter.executor.create_task(run)
        return await asyncio.wait_for(result, timeout)

    async def _rpc(self, name, data, command_id):
        spec, adapter = self.guard(name), self.adapter
        if name in catalog.MOTION_INTERFACES and self.stream is not None:
            raise BridgeError(409, "已有关节／速度发布流，请先结束当前流")

        def prepare():
            if name not in self.clients:
                self.clients[name] = adapter.node.create_client(getattr(adapter.srv, name), spec["path"])
            return self.clients[name]

        client = await self.native(prepare)
        deadline = time.monotonic() + 2.5
        while not client.service_is_ready():
            if time.monotonic() >= deadline:
                raise BridgeError(503, "SDK 服务尚未发现：" + spec["path"])
            await asyncio.sleep(0.05)
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        pending = []

        def complete(value=None, error=None):
            if not result.done():
                result.set_exception(error) if error else result.set_result(value)

        def received(future):
            try:
                loop.call_soon_threadsafe(complete, future.result())
            except Exception as exc:
                loop.call_soon_threadsafe(complete, None, exc)

        def dispatch():
            if result.done():
                return
            try:
                self.guard(name)
                _, conflicts = inspect_service_sources(adapter.node, {name: (spec["path"], name)})
                if conflicts.get(name):
                    raise BridgeError(409, conflicts[name])
                request = populate(getattr(adapter.srv, name).Request(), spec["schema"], data, adapter.msg)
                stamp_headers(request, adapter.node.get_clock().now().to_msg())
                if name == "PlayTts" and not request.tts_req.trace_id:
                    request.tts_req.trace_id = command_id
                if result.done():
                    return
                future = client.call_async(request)
                pending.append(future)
                future.add_done_callback(received)
            except Exception as exc:
                loop.call_soon_threadsafe(complete, None, exc)

        adapter.executor.create_task(dispatch)
        try:
            response = await asyncio.wait_for(result, 5)
            common = getattr(response, "response", getattr(response, "header", None))
            code = getattr(common, "code", getattr(getattr(common, "header", None), "code", None))
            state = getattr(getattr(common, "status", getattr(common, "state", None)), "value", None)
            accepted = code == 0 and (state in (0, 1, 200, 300, 400) or name == "GetMcAction")
            if name == "PlayTts":
                accepted = accepted and response.tts_resp.is_success is True
            return {
                "status": ("accepted" if spec["write"] else "completed") if accepted else "failed",
                "execution_confirmed": False,
                "detail": "已收到 SDK 原始响应；受理不等于物理执行完成"
                if accepted
                else "SDK 返回失败状态，请查看原始响应",
                "interface": name,
                "path": spec["path"],
                "response": plain(response),
                "vendor_code": code,
                "vendor_status": state,
            }
        except asyncio.TimeoutError:
            return {
                "status": "unknown",
                "execution_confirmed": False,
                "detail": "SDK 响应超时，未自动重发",
                "dispatched": bool(pending),
            }
        finally:
            for future in pending:
                if not future.done():
                    future.cancel()

    async def call(self, name, value, command_id):
        spec = self.guard(name)
        if spec["kind"] != "service":
            raise BridgeError(422, "此接口不是 ROS 服务")
        data = catalog.payload(name, value)
        receipt, new = self.sdk.ledger.begin(
            {
                "command_id": command_id,
                "device_uid": self.sdk.config.device_uid,
                "kind": "sdk:" + name,
                "arguments": data,
            }
        )
        if not new:
            return receipt

        async def dispatch():
            try:
                if self.operation_lock.locked():
                    raise BridgeError(409, "SDK 调用忙，未排队或发送此请求")
                async with self.operation_lock:
                    result = await self._rpc(name, data, command_id)
            except BridgeError as exc:
                result = {"status": "failed", "detail": exc.detail, "execution_confirmed": False}
            except Exception as exc:
                result = {
                    "status": "unknown",
                    "detail": f"{type(exc).__name__}: {str(exc)[:400]}",
                    "execution_confirmed": False,
                }
            return self.sdk.ledger.finish(command_id, result)

        task = asyncio.create_task(dispatch())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return await asyncio.shield(task)

    async def topic(self, name):
        spec, adapter = catalog.entry(name), self.adapter
        if spec["kind"] != "subscribe":
            raise BridgeError(422, "此接口不是读取话题")

        def subscribe():
            if name in self.subscriptions:
                return
            from rclpy.qos import QoSProfile, ReliabilityPolicy

            qos = QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE
                if spec["qos"] == "reliable"
                else ReliabilityPolicy.BEST_EFFORT,
            )

            def sample(message, path):
                with self.sample_lock:
                    if self.closing:
                        return
                    self.samples[name] = {
                        "received_at": now(),
                        "clock": time.monotonic(),
                        "path": path,
                        "message": plain(message),
                    }
                    value = {key: item for key, item in self.samples[name].items() if key != "clock"}
                    for loop, queue in self.watchers.get(name, []):

                        def deliver(queue=queue, value=value):
                            if queue.full():
                                queue.get_nowait()
                            queue.put_nowait(value)

                        if not loop.is_closed():
                            loop.call_soon_threadsafe(deliver)

            self.subscriptions[name] = [
                adapter.node.create_subscription(
                    getattr(adapter.msg, name), path, lambda message, path=path: sample(message, path), qos
                )
                for path in [spec["path"], *spec.get("aliases", [])]
            ]

        await self.native(subscribe)
        with self.sample_lock:
            value = deepcopy(self.samples.get(name))
        if not value:
            return {
                "interface": name,
                "availability": "unknown",
                "message": None,
                "detail": "已订阅，尚未收到此话题样本",
            }
        age = time.monotonic() - value.pop("clock")
        return {
            "interface": name,
            "availability": "fresh" if age <= 3 else "stale",
            "age_seconds": round(age, 3),
            **value,
        }

    @asynccontextmanager
    async def watch(self, name):
        await self.topic(name)
        queue = asyncio.Queue(maxsize=4)
        watcher = (asyncio.get_running_loop(), queue)
        with self.sample_lock:
            self.watchers.setdefault(name, []).append(watcher)
        try:
            yield queue
        finally:
            with self.sample_lock:
                self.watchers[name].remove(watcher)

    async def publisher(self, name):
        spec, adapter = self.guard(name), self.adapter

        def create():
            if name not in self.publishers:
                from rclpy.qos import QoSProfile, ReliabilityPolicy

                qos = QoSProfile(
                    depth=10,
                    reliability=ReliabilityPolicy.RELIABLE
                    if spec["qos"] == "reliable"
                    else ReliabilityPolicy.BEST_EFFORT,
                )
                self.publishers[name] = adapter.node.create_publisher(
                    getattr(adapter.msg, name), spec["path"], qos
                )
            return self.publishers[name]

        return await self.native(create)

    async def joint_targets(self, name, data):
        # Subscribe only after an explicit request. Never synthesize missing joints as zero.
        sample = await self.topic("JointStateArray")
        deadline = time.monotonic() + 2
        while sample["availability"] != "fresh" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            sample = await self.topic("JointStateArray")
        if sample["availability"] != "fresh" or sample.get("age_seconds", 9) > 0.5:
            raise BridgeError(409, "没有新鲜关节反馈，无法补全其他关节；请检查机器人开发模式与状态话题")
        providers = await self.native(
            lambda: self.adapter.node.get_publishers_info_by_topic(catalog.ENTRIES["JointStateArray"]["path"])
        )
        if len(providers) != 1 or providers[0].topic_type != "aimdk_msgs/msg/JointStateArray":
            raise BridgeError(409, "关节状态话题来源不唯一或类型不匹配")
        states = {row["name"]: row for row in sample["message"]["joints"]}
        names = [
            key for key, limits in catalog.JOINTS.items() if name == "JointCommandArray" or limits["upper"]
        ]
        if any(key not in states or states[key].get("position") is None for key in names):
            raise BridgeError(409, "关节反馈不完整，不能补全完整控制帧")
        selected = {row["name"]: row for row in data["joints"]}
        target = []
        for key in names:
            if key in selected:
                target.append(selected[key])
            else:
                target.append(
                    {
                        "name": key,
                        "position": states[key]["position"],
                        "velocity": 0.0,
                        "effort": 0.0,
                        "stiffness": 20.0,
                        "damping": 2.0 if name == "JointCommandArray" else 3.0,
                    }
                )
        if name == "McCustomJointCommand":
            mode = await self.adapter.motion_mode()
            constants = catalog.DEFINITIONS["McAction"]["constants"]
            expected = constants.get("BIPED_CUSTOM_UPPER")
            if mode["mode"] != expected or mode["status"] != 100:
                raise BridgeError(
                    409, "上肢关节需先进入 BIPED_CUSTOM_UPPER 且状态为 RUNNING；页面提供显式切换入口"
                )
        return {**data, "joints": target}, {key: states[key]["position"] for key in names}

    async def publish(self, name, value, command_id, *, rate_hz=1, duration_ms=1000):
        spec = self.guard(name)
        if spec["kind"] != "publish":
            raise BridgeError(422, "此接口不是发布话题")
        if (
            type(rate_hz) is not int
            or not 1 <= rate_hz <= 1000
            or type(duration_ms) is not int
            or not 1 <= duration_ms <= 10000
        ):
            raise BridgeError(422, "频率为 1–1000 Hz，持续时长为 1–10000 ms")
        if name == "JointCommandArray" and rate_hz < 500:
            raise BridgeError(422, "厂家 HAL 关节流要求 500–1000 Hz")
        if name == "McCustomJointCommand" and rate_hz != 100:
            raise BridgeError(422, "厂家自定义上肢流使用 100 Hz")
        data = catalog.payload(name, value)
        arguments = {"payload": data, "rate_hz": rate_hz, "duration_ms": duration_ms}
        receipt, new = self.sdk.ledger.begin(
            {
                "command_id": command_id,
                "device_uid": self.sdk.config.device_uid,
                "kind": "sdk-stream:" + name,
                "arguments": arguments,
            }
        )
        if not new:
            return receipt
        if self.operation_lock.locked():
            return self.sdk.ledger.finish(
                command_id,
                {"status": "failed", "detail": "SDK 调用忙，未排队或发布", "execution_confirmed": False},
            )
        async with self.operation_lock:
            try:
                if self.stream is not None:
                    raise BridgeError(409, "已有 SDK 发布流，请先结束当前流")
                start_positions = {}
                if name in catalog.JOINT_TOPICS:
                    data, start_positions = await self.joint_targets(name, data)
                publisher = await self.publisher(name)
                stream = TopicStream(command_id, name, rate_hz, duration_ms, time.monotonic() + 1.5)
                self.stream = stream
                result = self.sdk.ledger.finish(
                    command_id,
                    {
                        "status": "running",
                        "detail": "发布流已开始；需每 500 ms 续租",
                        "execution_confirmed": False,
                        "interface": name,
                    },
                )
                stream.task = asyncio.create_task(
                    self._stream_worker(stream, publisher, data, start_positions)
                )
                return result
            except BridgeError as exc:
                return self.sdk.ledger.finish(
                    command_id, {"status": "failed", "detail": exc.detail, "execution_confirmed": False}
                )
            except Exception as exc:
                return self.sdk.ledger.finish(
                    command_id, {"status": "failed", "detail": str(exc)[:400], "execution_confirmed": False}
                )

    async def _stream_worker(self, stream, publisher, data, starts):
        try:
            result = await asyncio.to_thread(self._publish_loop, stream, publisher, data, starts)
        except Exception as exc:
            result = {"status": "unknown", "detail": str(exc)[:400]}
        self.sdk.ledger.finish(
            stream.command_id,
            {
                **result,
                "execution_confirmed": False,
                "output_finished": True,
                "packets_sent": stream.sent,
                "cleanup_sent": stream.cleanup_sent,
            },
        )
        if self.stream is stream:
            self.stream = None

    def _publish_loop(self, stream, publisher, data, starts):
        # A dedicated publishing thread avoids coupling servo timing to HTTP or the
        # adapter's discovery executor. This is not a hard real-time guarantee.
        adapter = self.adapter
        message = populate(getattr(adapter.msg, stream.interface)(), stream.interface, data, adapter.msg)
        started = time.monotonic()
        deadline = started + stream.duration_ms / 1000
        tick, interval = started, 1 / stream.rate_hz
        last_stamp = 0
        targets = {joint["name"]: joint["position"] for joint in data.get("joints", [])}
        last_positions = dict(starts)
        max_gap = 0.0
        previous = None

        def send():
            nonlocal last_stamp
            stamp = adapter.node.get_clock().now().to_msg()
            value = max(stamp.sec * 10**9 + stamp.nanosec, last_stamp + 1)
            stamp.sec, stamp.nanosec = divmod(value, 10**9)
            last_stamp = value
            stamp_headers(message, stamp, stream.sent)
            publisher.publish(message)

        try:
            while not stream.stop.is_set() and time.monotonic() < min(deadline, stream.expires_at):
                clock = time.monotonic()
                if previous is not None:
                    max_gap = max(max_gap, clock - previous)
                previous = clock
                if starts:
                    fraction = min(1.0, (clock - started) / min(0.5, stream.duration_ms / 1000))
                    for joint in message.joints:
                        value = starts[joint.name] + (targets[joint.name] - starts[joint.name]) * fraction
                        # Enforce the vendor's per-frame position increment limit.
                        joint.position = max(
                            last_positions[joint.name] - 0.2, min(last_positions[joint.name] + 0.2, value)
                        )
                        last_positions[joint.name] = joint.position
                send()
                stream.sent += 1
                tick += interval
                # Do not send a burst of overdue packets after a scheduler stall.
                tick = max(tick, time.monotonic())
                stream.stop.wait(max(0, tick - time.monotonic()))
            return {
                "status": "stopped",
                "detail": "本次发布已结束；以机器人实际反馈判断运动状态",
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "max_gap_ms": round(max_gap * 1000, 3),
                "requested_rate_hz": stream.rate_hz,
            }
        finally:
            if stream.sent and stream.interface == "JointCommandArray":
                for joint in message.joints:
                    joint.velocity, joint.effort, joint.stiffness, joint.damping = 0.0, 0.0, 0.0, 5.0
                send()
                stream.cleanup_sent = True
            elif stream.sent and stream.interface == "McLocomotionVelocity":
                for key in ("forward_velocity", "lateral_velocity", "angular_velocity", "pitch_velocity"):
                    setattr(message, key, 0.0)
                send()
                stream.cleanup_sent = True

    def stream_status(self):
        stream = self.stream
        return (
            None
            if stream is None
            else {
                "command_id": stream.command_id,
                "interface": stream.interface,
                "rate_hz": stream.rate_hz,
                "duration_ms": stream.duration_ms,
                "packets_sent": stream.sent,
            }
        )

    def heartbeat(self, command_id):
        if (
            self.stream is None
            or self.stream.command_id != command_id
            or time.monotonic() >= self.stream.expires_at
        ):
            raise BridgeError(409, "发布租约已结束")
        self.guard(self.stream.interface)
        self.stream.expires_at = time.monotonic() + 1.5
        return {"status": "running", "lease_ms": 1500}

    async def stop(self, command_id):
        if self.stream and self.stream.command_id == command_id:
            self.stream.stop.set()
            await asyncio.shield(self.stream.task)
        return self.sdk.receipt(command_id)

    async def close(self):
        self.closing = True
        with self.sample_lock:
            for watchers in self.watchers.values():
                for _, queue in watchers:
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(None)
        if self.stream:
            self.stream.stop.set()
            await asyncio.shield(self.stream.task)
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.sdk.adapter is None or not (self.clients or self.publishers or self.subscriptions):
            return

        def destroy():
            for client in self.clients.values():
                self.adapter.node.destroy_client(client)
            for publisher in self.publishers.values():
                self.adapter.node.destroy_publisher(publisher)
            for subscriptions in self.subscriptions.values():
                for subscription in subscriptions:
                    self.adapter.node.destroy_subscription(subscription)

        await self.native(destroy)
