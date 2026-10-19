"""Optional rclpy adapter. Importing this module does not initialize ROS."""

import asyncio
import logging
import threading
import time
import uuid

from t1_lab._vendor.adapters.audio import PulseAudio
from t1_lab._vendor.contracts.catalog import (
    BridgeError,
    CAMERAS,
    COMMANDS,
    READ_SERVICES,
    SERVICES,
    TOPICS,
    now,
)
from t1_lab._vendor.adapters.ros.details import DetailCache, read_fields
from t1_lab._vendor.adapters.ros.motion_protocol import (
    MOTION_SERVICES,
    VELOCITY_TOPIC,
    input_result,
    mode_result,
    populate_motion_request,
    populate_velocity,
)
from t1_lab._vendor.services.motion_policy import response_result
from t1_lab._vendor.adapters.ros.protocol import (
    command_result,
    frame_result,
    populate_request,
    request_details,
)
from t1_lab._vendor.adapters.ros.loader import load_ros
from t1_lab._vendor.adapters.ros.telemetry import Telemetry, field
from t1_lab._vendor.adapters.ros.service_discovery import inspect_service_sources, MotionServiceReadiness

MOTION_READ_SERVICES = {kind: SERVICES[kind] for kind in ("get_mc_action", "get_input_source")}
MOTION_CLIENT_SERVICES = {**MOTION_READ_SERVICES, **MOTION_SERVICES}
INITIAL_STATE_TIMEOUT = 3.0


def motion_enabled(config):
    return config.family == "t1" and not config.read_only and config.motion["enabled"]


def enabled_services(config):
    services = {kind: spec for kind, spec in SERVICES.items() if kind in READ_SERVICES or kind == "frame"}
    if not config.read_only:
        services.update({kind: SERVICES[kind] for kind in config.enabled_commands})
    if motion_enabled(config):
        services.update(MOTION_SERVICES)
    return services


def graph_topics(node):
    result = {}
    for key, (topic, _) in TOPICS.items():
        endpoints = node.get_publishers_info_by_topic(topic)
        result[key] = tuple(
            sorted(
                (bytes(e.endpoint_gid).hex() if any(e.endpoint_gid) else "", e.topic_type) for e in endpoints
            )
        )
    return result


def service_sources(node, services=None):
    return inspect_service_sources(node, SERVICES if services is None else services)[0]


def unique_service(node, kind):
    return service_sources(node)[kind] is not None


class RosAdapter:
    def __init__(self, config):
        self.msg, self.srv = load_ros(config)
        msg = self.msg
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data

        self.config, self.rclpy = config, rclpy
        self.telemetry, self.audio = Telemetry(config), PulseAudio(config.audio)
        self.details = DetailCache(config, field)
        self.detail_task = None
        self.graph_lock = threading.RLock()
        self.context = Context()
        rclpy.init(args=[], context=self.context, domain_id=config.ros_domain_id)
        self.node = Node(
            "t1_lab_" + uuid.uuid4().hex[:12],
            namespace="/roboweave",
            context=self.context,
            use_global_arguments=False,
            start_parameter_services=False,
            enable_rosout=False,
        )
        self.services = enabled_services(config)
        self.providers = {k: False for k in self.services}
        self.provider_signatures = {k: None for k in self.services}
        self.provider_generations = {k: 0 for k in self.services}
        self.provider_tokens = {k: None for k in self.services}
        self.services_observed_at = None
        self.clients = {
            kind: self.node.create_client(getattr(self.srv, type_name), service)
            for kind, (service, type_name) in self.services.items()
        }
        self.motion_services = (
            MotionServiceReadiness(
                self.clients, MOTION_CLIENT_SERVICES if motion_enabled(config) else MOTION_READ_SERVICES
            )
            if config.family == "t1"
            else None
        )
        self.motion_source = "roboweave_" + uuid.uuid4().hex[:24]
        self.motion_registered = False
        self.velocity_sequence = 0
        # Depth 10 uses reliable/volatile QoS, matching the pinned SDK. No output at startup.
        self.velocity_publisher = (
            self.node.create_publisher(msg.McLocomotionVelocity, VELOCITY_TOPIC, 10)
            if motion_enabled(config)
            else None
        )
        self.subscriptions = []
        for key, (topic, type_name) in TOPICS.items():
            # Humble passes only the message; no invented MessageInfo publisher_gid.
            self.subscriptions.append(
                self.node.create_subscription(
                    getattr(msg, type_name),
                    topic,
                    lambda message, key=key: self._sample(key, message),
                    qos_profile_sensor_data,
                )
            )
        self.timer = self.node.create_timer(0.5, self._graph)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, name="bridge-ros", daemon=True)
        self.thread.start()
        self.frame_busy = False

    def _sample(self, key, message):
        try:
            self.telemetry.ingest(key, message)
        except (ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).warning("Discarded invalid %s sample", key)

    def _graph(self):
        with self.graph_lock:
            try:
                self.telemetry.observe_graph(graph_topics(self.node))
            except Exception:
                self.telemetry.observe_graph({k: None for k in TOPICS})
            try:
                signatures, conflicts = inspect_service_sources(self.node, self.services)
                self.services_observed_at = time.monotonic()
            except Exception:
                signatures = {k: None for k in self.services}
                conflicts = {}
                self.services_observed_at = None
            for kind, source in signatures.items():
                if source != self.provider_signatures[kind] or source is None:
                    self.provider_generations[kind] += 1
                self.provider_tokens[kind] = (
                    (self.provider_generations[kind], source) if source is not None else None
                )
            self.provider_signatures = signatures
            self.providers = {k: source is not None for k, source in signatures.items()}
            if getattr(self, "motion_services", None) is not None:
                self.motion_services.conflicts = conflicts
                self.motion_services.refresh()

    async def start(self):
        self.detail_task = asyncio.create_task(self._poll_details(), name="bridge-read-only-details")
        # HTTP readiness must allow DDS discovery and the first read-only poll.
        # Otherwise the first connection advertises every interface as missing.
        # An absent robot still permits the SDK to start after this bounded wait.
        deadline = time.monotonic() + INITIAL_STATE_TIMEOUT
        while time.monotonic() < deadline:
            if self.evidence()["source_verified"]:
                break
            await asyncio.sleep(0.1)

    async def _read_detail(self, kind):
        if not self.providers.get(kind):
            self.details.failed(kind, "只读服务尚未唯一验证")
            return
        try:
            response, stamp = await self._call(kind, {}, "", include_source=True)
            fields = read_fields(kind, response, self.config.family, field)
            self.details.record(kind, fields, stamp)
        except Exception as exc:
            detail = (
                "调用超时"
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                else str(getattr(exc, "detail", exc))[:400]
            )
            self.details.failed(kind, f"{READ_SERVICES[kind][0]}：{type(exc).__name__} {detail}")

    async def _poll_details(self):
        while True:
            await asyncio.gather(*(self._read_detail(kind) for kind in READ_SERVICES))
            await asyncio.sleep(2)

    def evidence(self):
        with self.graph_lock:
            evidence = self.telemetry.evidence()
            if evidence["source_verified"] or self.telemetry.conflict() is not None:
                return evidence
            if self.services_observed_at is not None and time.monotonic() - self.services_observed_at <= 2:
                kind = self.details.evidence(0, self.provider_tokens)
                if kind is not None:
                    return {
                        "source_count": 1,
                        "source_verified": True,
                        "source_key": kind,
                        "source_method": "service_singleton_and_fresh_response",
                        "source_reason": "只读服务提供者唯一且已收到有效回复；不证明整机序列号或运动安全",
                    }
            return evidence

    def capabilities(self):
        graph_current = (
            self.services_observed_at is not None and time.monotonic() - self.services_observed_at <= 2
        )
        capabilities = []
        for name in (
            "state",
            "frame",
            "audio",
            *sorted(COMMANDS),
            *READ_SERVICES,
            *CAMERAS[self.config.family],
        ):
            base = "frame" if name in CAMERAS[self.config.family] else name
            available = (
                True
                if base == "state"
                else self.audio.available
                if base == "audio"
                else graph_current and self.providers.get(base, False)
            )
            enabled = base not in COMMANDS or base in self.config.enabled_commands
            if base == "state":
                reason = "状态读取通道就绪；缺失或过期字段单独标记，不表示设备遥测已收到"
            elif not available:
                reason = (
                    "尚未配置机器人拾音端点与声道"
                    if base == "audio"
                    else "此接口尚未发现唯一提供者、类型不匹配或服务图已过期"
                )
            elif not enabled:
                reason = "此命令尚未在 SDK settings.json 的 enabled_commands 中启用"
            else:
                reason = "此接口可独立测试；实际受理或完成以调用回执为准"
            capabilities.append(
                {
                    "name": name,
                    "available": bool(available and enabled),
                    "level": "probed",
                    "source": "ros-graph/local-config",
                    "checked_at": now(),
                    "reason": reason,
                }
            )
        return capabilities

    def state(self):
        with self.graph_lock:
            data = self.telemetry.snapshot()
            # Getter samples follow their own provider generation, not the battery publisher.
            graph_current = (
                self.services_observed_at is not None and time.monotonic() - self.services_observed_at <= 2
            )
            details = self.details.snapshot(graph_current, 0, self.provider_tokens)
        for group, fields in details.items():
            data.setdefault(group, []).extend(fields)
        data.setdefault("audio", []).insert(
            0, field("capture", "采集状态", "capturing" if self.audio.busy else "idle")
        )
        return data

    def ensure_source(self):
        if not self.evidence()["source_verified"]:
            raise BridgeError(409, "设备来源未唯一验证或已过期")

    def ensure_motion_source(self):
        if self.config.motion.get("runtime", {}).get("version") == 2:
            reason = self.motion_service_reason(MOTION_READ_SERVICES)
            if reason:
                raise BridgeError(409, reason)
            return
        if not self.telemetry.evidence("battery")["source_verified"]:
            raise BridgeError(409, "运动需要独立核验的实时电池反馈；连接就绪不代表运动可用")

    async def _call(self, kind, arguments, trace_id, *, include_source=False, permit=None, diagnostics=None):
        if self.config.read_only and kind not in READ_SERVICES and kind != "frame":
            raise BridgeError(403, "只读模式：禁止向机器人发送控制请求")
        if kind not in enabled_services(self.config):
            raise BridgeError(403, "此 SDK 服务已被当前配置关闭")
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        pending = []
        source_stamp = []

        def complete(value=None, error=None):
            if not result.done():
                if error is not None:
                    result.set_exception(error)
                else:
                    result.set_result(value)

        def done(future):
            if result.done() or future.cancelled():
                return
            try:
                value = future.result()
                loop.call_soon_threadsafe(complete, value)
            except Exception as exc:
                loop.call_soon_threadsafe(complete, None, exc)

        def dispatch():
            # A timeout before this callback runs must not leave a late physical dispatch.
            if result.done():
                return
            try:
                self._graph()
                # Ordinary RPCs depend only on their own provider, not unrelated telemetry.
                if kind in MOTION_SERVICES:
                    self.ensure_motion_source()
                if (
                    kind in MOTION_CLIENT_SERVICES
                    and self.config.motion.get("runtime", {}).get("version") == 2
                ):
                    reason = self.motion_service_reason((kind,))
                    if reason:
                        raise BridgeError(409, reason)
                elif not self.providers.get(kind) or not self.clients[kind].service_is_ready():
                    raise BridgeError(409, "服务来源未知、不唯一或尚未就绪")
                if permit is not None and not permit():
                    raise BridgeError(409, "运动已撤销或连接来源变化；未派发")
                if include_source:
                    source_stamp.append((0, self.provider_tokens.get(kind)))
                services = getattr(self, "services", SERVICES)
                request = getattr(self.srv, services[kind][1]).Request()
                stamp = self.node.get_clock().now().to_msg()
                if kind in MOTION_SERVICES:
                    populate_motion_request(kind, request, arguments, stamp)
                else:
                    populate_request(kind, request, arguments, trace_id, stamp)
                if result.done():
                    return
                if diagnostics is not None:
                    diagnostics["parameters"] = request_details(kind, request)
                    diagnostics["dispatched"] = True
                future = self.clients[kind].call_async(request)
                pending.append(future)
                future.add_done_callback(done)
            except Exception as exc:
                loop.call_soon_threadsafe(complete, None, exc)

        self.executor.create_task(dispatch)
        try:
            response = await asyncio.wait_for(result, 3)
            if include_source:
                # Reject a response from a provider which changed while the RPC was in flight.
                with self.graph_lock:
                    if source_stamp[0] != (0, self.provider_tokens.get(kind)):
                        raise BridgeError(409, "只读服务来源已变化；丢弃旧回复")
            return (response, source_stamp[0]) if include_source else response
        finally:
            # Cancelling a local future only releases bookkeeping, not robot playback.
            for future in pending:
                if not future.done():
                    future.cancel()

    async def command(self, kind, args, command_id):
        if kind not in self.config.enabled_commands:
            raise BridgeError(409, "此命令已在 enabled_commands 配置中关闭")
        services = getattr(self, "services", SERVICES)
        diagnostics = {
            "service": services[kind][0],
            "type": "aimdk_msgs/srv/" + services[kind][1],
            "dispatched": False,
        }
        started = time.monotonic()
        try:
            response = await self._call(kind, args, command_id, diagnostics=diagnostics)
            result = command_result(kind, response)
        except BridgeError as exc:
            result = {
                "status": "failed",
                "detail": exc.detail,
                "error_type": type(exc).__name__,
                "execution_confirmed": False,
            }
        except asyncio.TimeoutError:
            result = {
                "status": "unknown",
                "detail": "SDK 调用超时；执行状态未知，不自动重发",
                "error_type": "TimeoutError",
                "execution_confirmed": False,
            }
        except Exception as exc:
            result = {
                "status": "unknown",
                "detail": "SDK 调用异常；执行状态未知，不自动重发",
                "error_type": type(exc).__name__,
                "execution_confirmed": False,
            }
        return {
            **result,
            "sdk_request": diagnostics,
            "sdk_elapsed_ms": round((time.monotonic() - started) * 1000),
        }

    async def frame(self, camera_id):
        if self.frame_busy:
            raise BridgeError(409, "相机采集忙")
        self.frame_busy = True
        try:
            return frame_result(await self._call("frame", {"camera_id": camera_id}, ""), camera_id)
        finally:
            self.frame_busy = False

    async def motion_mode(self):
        return mode_result(await self._call("get_mc_action", {}, ""))

    def motion_generation(self):
        with self.graph_lock:
            tokens = tuple(
                self.provider_tokens.get(kind)
                for kind in ("get_mc_action", "get_input_source", *MOTION_SERVICES)
            )
            if self.config.motion.get("runtime", {}).get("version") == 2:
                self.motion_services.refresh()
                return (self.motion_services.generation, tokens)
            return (self.telemetry.generation(), tokens)

    def motion_service_reason(self, required):
        with self.graph_lock:
            return self.motion_services.reason(required)

    def motion_unavailable_reason(self, kind, stop_mode):
        required = ["get_mc_action", "get_input_source", "motion_mode"]
        if kind != "mode":
            required.append("motion_preset" if kind == "preset" else "motion_input")
        return self.motion_service_reason(required)

    def motion_available(self, kind, stop_mode):
        if self.config.motion.get("runtime", {}).get("version") == 2:
            return self.motion_unavailable_reason(kind, stop_mode) is None
        required = {"get_mc_action", "get_input_source", "motion_mode"}
        if kind != "mode":
            required.add("motion_preset" if kind == "preset" else "motion_input")
        if stop_mode is not None:
            required.add("motion_mode")
        return all(self.providers.get(key, False) for key in required)

    async def motion_control(self):
        return input_result(await self._call("get_input_source", {}, ""))

    async def motion_acquire(self, priority, timeout_ms, *, permit=None):
        # Track an attempted registration too: timeout does not prove it never happened.
        self.motion_registered = True
        return response_result(
            await self._call(
                "motion_input",
                {
                    "operation": "add",
                    "source": self.motion_source,
                    "priority": priority,
                    "timeout_ms": timeout_ms,
                },
                "",
                permit=permit,
            )
        )

    async def motion_release(self, priority, timeout_ms, *, permit=None):
        if not self.motion_registered:
            return {"status": "accepted", "execution_confirmed": False, "detail": "本实例未注册控制源"}
        result = response_result(
            await self._call(
                "motion_input",
                {
                    "operation": "delete",
                    "source": self.motion_source,
                    "priority": priority,
                    "timeout_ms": timeout_ms,
                },
                "",
                permit=permit,
            )
        )
        if result["status"] == "accepted":
            self.motion_registered = False
        return result

    async def motion_wave(self, preset, *, permit=None):
        args = {"preset": preset}
        if self.config.motion.get("runtime", {}).get("version") == 2:
            args["interrupt"] = True
        return response_result(await self._call("motion_preset", args, "", permit=permit))

    async def motion_stop_mode(self, mode, *, permit=None):
        # The executor chooses a pinned normal transition or an explicit legacy stop.
        return response_result(await self._call("motion_mode", {"mode": mode}, "", permit=permit))

    async def motion_velocity(self, velocity, *, permit):
        if not motion_enabled(self.config):
            raise BridgeError(403, "运动控制关闭：禁止发布机器人速度")
        loop = asyncio.get_running_loop()
        result = loop.create_future()

        def complete(error=None):
            if not result.done():
                result.set_exception(error) if error else result.set_result(None)

        def publish():
            if result.done():
                return
            try:
                self.ensure_motion_source()
                if not permit() or not self.motion_registered or self.velocity_publisher is None:
                    raise BridgeError(409, "速度输出租约失效或控制源未注册")
                message = populate_velocity(
                    self.msg.McLocomotionVelocity(),
                    self.motion_source,
                    velocity,
                    self.node.get_clock().now().to_msg(),
                    self.velocity_sequence,
                )
                self.velocity_sequence += 1
                self.velocity_publisher.publish(message)
                loop.call_soon_threadsafe(complete)
            except Exception as exc:
                loop.call_soon_threadsafe(complete, exc)

        self.executor.create_task(publish)
        await asyncio.wait_for(result, 0.2)

    async def close(self):
        if self.detail_task is not None:
            self.detail_task.cancel()
            await asyncio.gather(self.detail_task, return_exceptions=True)
        await self.audio.close()
        await asyncio.to_thread(self.executor.shutdown, timeout_sec=2)
        await asyncio.to_thread(self.thread.join, 2)
        self.node.destroy_node()
        self.context.try_shutdown()
