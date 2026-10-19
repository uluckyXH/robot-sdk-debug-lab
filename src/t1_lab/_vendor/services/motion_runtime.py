"""Bounded movement, independent stop, leases and durable uncertain outcomes."""

import asyncio
from datetime import datetime, timezone
import math
import logging
import re
import time

from t1_lab._vendor.contracts.catalog import BridgeError
from t1_lab._vendor.services.motion_policy import ALLOWED_BMS_BITS, _field
from t1_lab._vendor.services.motion_runtime_policy import ACTIONS, UNVERIFIED
from t1_lab._vendor.services.t1_execution import T1Execution

SCOPE_FIELDS = {
    "device_uid",
    "command_id",
    "binding_revision",
    "connection_epoch",
    "source",
    "control_revision",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
LOG = logging.getLogger("roboweave.bridge.motion")


class Interrupted(Exception):
    pass


class MotionRuntime(T1Execution):
    def __init__(self, config, adapter, ledger, *, clock=time.monotonic, wall_clock=time.time):
        self.config, self.adapter, self.ledger = config, adapter, ledger
        self.clock, self.wall_clock = clock, wall_clock
        self.policy = config.motion.get("runtime", {})
        self.active = None
        self.worker = None
        self.last_preset = None
        self.closing = False

    def validate(self, body, *, action=False):
        allowed = SCOPE_FIELDS | {"session_id"} | ({"action"} if action else set())
        required = allowed - {"session_id"}
        if not isinstance(body, dict) or set(body) - allowed or not required <= body.keys():
            raise BridgeError(422, "运动请求身份或字段不完整")
        if body["device_uid"] != self.config.device_uid:
            raise BridgeError(409, "运动设备身份不符")
        if type(body["binding_revision"]) is not int or body["binding_revision"] < 1:
            raise BridgeError(422, "需要正整数 binding_revision")
        if type(body["control_revision"]) is not int or not 0 <= body["control_revision"] < 2**63:
            raise BridgeError(422, "control_revision 须为非负 64 位整数")
        for key in ("command_id", "connection_epoch"):
            if not isinstance(body[key], str) or not IDENTIFIER.fullmatch(body[key]):
                raise BridgeError(422, key + " 格式无效")
        if body["source"] not in ("manual", "model"):
            raise BridgeError(422, "source 只能为 manual 或 model")
        session = body.get("session_id")
        if session is not None and (not isinstance(session, str) or not IDENTIFIER.fullmatch(session)):
            raise BridgeError(422, "session_id 格式无效")
        if body["source"] == "model" and session is None:
            raise BridgeError(422, "模型运动必须绑定 session_id")
        if action and (not isinstance(body["action"], str) or body["action"] not in ACTIONS):
            raise BridgeError(422, "action 不在有限动作表中")

    def supported(self):
        from t1_lab._vendor.adapters.ros.adapter import RosAdapter

        return self.config.family == "t1" and not self.config.simulated and type(self.adapter) is RosAdapter

    def _source(self):
        evidence = self.adapter.evidence()
        return (
            evidence.get("source_verified") is True
            and type(evidence.get("source_count")) is int
            and evidence["source_count"] == 1
        )

    def _generation(self):
        return self.adapter.motion_generation()

    def _fresh(self, group, key):
        return _field(
            {"telemetry": self.adapter.state()},
            group,
            key,
            datetime.now(timezone.utc),
            self.config.stale_after,
        )

    def _battery_ok(self):
        charge = self._fresh("battery", "remaining_capacity_percentage")
        status, power = self._fresh("battery", "status"), self._fresh("battery", "power_request")
        if not charge or not status or not power:
            return False
        value, bits = charge.get("value"), status.get("value")
        return (
            type(value) in (int, float)
            and math.isfinite(value)
            and self.config.motion["minimum_battery_percent"] <= value <= 100
            and charge.get("scale_verified") is True
            and type(bits) is int
            and 0 <= bits < 2**32
            and bits & ~ALLOWED_BMS_BITS == 0
            and type(power.get("value")) is int
            and power["value"] == 0
        )

    def reason(self, name, *, source="manual"):
        if self.policy.get("version") == 2:
            return self.reason_t1(name)
        if not self.supported():
            return "本运动执行器仅适用于 T1 真实 ROS 适配器；模拟流程继续使用原预设入口"
        if not self.config.motion["enabled"]:
            return "运动已在本机 motion.enabled 配置中关闭"
        if not self.policy:
            return UNVERIFIED
        if name not in self.policy.get("profiles", {}):
            return "本机 motion.runtime.profiles 尚未配置动作 " + name
        if self.closing:
            return "SDK 正在退出"
        if self.ledger.motion_pending or self.active:
            return "有运行中或未决运动；必须先停止或核验原记录"
        last = self.ledger.motion_last_started
        elapsed = self.wall_clock() - last if last is not None else None
        if elapsed is not None and (
            not math.isfinite(elapsed) or elapsed < self.config.motion["minimum_interval_seconds"]
        ):
            return "尚未到下一次运动允许时间"
        if not self._source():
            return "尚未收到唯一且新鲜的机器人来源反馈"
        if not self._battery_ok():
            return "电池反馈缺失、过期、比例未确认或不满足当前电量/充电条件"
        profile = self.policy["profiles"][name]
        if not self.adapter.motion_available(ACTIONS[name]["kind"], profile["stop"]["mode"]):
            return "运动或停止所需 SDK 服务未唯一发现"
        if source == "model" and profile["stop"]["feedback"] != "validated_mode":
            return "此配置结束需人工观察，尚不能向模型开放"
        mode = self._fresh("motion", "mc_action")
        state = self._fresh("motion", "mc_action_status")
        ready = self._fresh("motion", "mc_switch_ready")
        if (
            not mode
            or mode.get("value") != profile["mode"]
            or not state
            or state.get("value") != 100
            or not ready
            or ready.get("value") is not True
        ):
            return "需要当前动作指定模式的新鲜 RUNNING/switch_ready 反馈；不会自动变形"
        return None

    def info(self):
        actions = []
        for name, spec in ACTIONS.items() if self.config.family == "t1" else ():
            profile = self.policy.get("profiles", {}).get(name)
            reason = self.reason(name)
            actions.append(
                {
                    "action": name,
                    "label": spec["label"],
                    "kind": spec["kind"],
                    "available": reason is None,
                    "ready": reason is None,
                    "hardware_verified": False,
                    "model_available": reason is None and self.reason(name, source="model") is None,
                    "reason": reason,
                    "required_mc_action": profile["mode"] if profile else spec.get("mode"),
                }
            )
        current = self.ledger.get(self.active["body"]["command_id"]) if self.active else None
        if current is None:
            pending = sorted(self.ledger.motion_pending)
            current = self.ledger.get(pending[0]) if pending else None
        return {
            "actions": actions,
            "current": current,
            "lease_ms": self.policy.get("lease_ms"),
            **(
                self.ledger.motion_fence
                or {"control_revision": None, "binding_revision": None, "connection_epoch": None}
            ),
            "hardware_execution_available": bool(
                self.supported() and self.config.motion["enabled"] and self.policy
            ),
            "execution_supported": self.supported(),
            "stop": {
                "available": self.supported(),
                "active": self.active is not None,
                "reason": None
                if self.active
                else "可撤销旧控制代次；当前实例无可停止任务，重启后未决记录需现场核验",
            },
        }

    def _begin(self, body, kind):
        args = {k: v for k, v in body.items() if k not in ("device_uid", "command_id")}
        return self.ledger.begin(
            {
                "device_uid": body["device_uid"],
                "command_id": body["command_id"],
                "kind": kind,
                "arguments": args,
            }
        )

    def _replay(self, body, kind):
        try:
            self.ledger.get(body["command_id"])
        except BridgeError as exc:
            if exc.status == 404:
                return None
            raise
        return self._begin(body, kind)[0]

    def _fence(self, body, *, stopping=False):
        # Validate the current bridge epoch before allowing a request to advance its fence.
        if (
            not body["command_id"].startswith(self.ledger.epoch + ":")
            or len(body["command_id"]) <= len(self.ledger.epoch) + 1
        ):
            raise BridgeError(409, "命令 epoch 已失效")
        current = self.ledger.motion_fence
        if current is None or any(
            body[key] != current[key] for key in ("binding_revision", "connection_epoch")
        ):
            raise BridgeError(409, "运动连接尚未 bind 或绑定已失效；请求不能自行换绑")
        if (
            not stopping
            and body["source"] == "model"
            and body["control_revision"] != current["control_revision"]
        ):
            raise BridgeError(409, "模型不能推进控制代次，必须使用本轮已授权快照")
        changed = self.ledger.fence_motion(body)
        if changed and self.active is not None:
            self.active["reason"] = "control_revoked"
            self.active["stop"].set()

    def bind(self, body):
        keys = {"device_uid", "binding_revision", "connection_epoch", "control_revision"}
        if not isinstance(body, dict) or set(body) != keys:
            raise BridgeError(
                422, "bind 字段必须为 device_uid/binding_revision/connection_epoch/control_revision"
            )
        self.validate({**body, "command_id": self.ledger.epoch + ":bind", "source": "manual"})
        if self.ledger.bind_motion(body) and self.active is not None:
            self.active["reason"] = "binding_replaced"
            self.active["stop"].set()
        return dict(self.ledger.motion_fence)

    async def request(self, body):
        self.validate(body, action=True)
        replay = self._replay(body, "motion_request")
        if replay is not None:
            return replay
        self._fence(body)
        reason = self.reason(body["action"], source=body["source"])
        receipt, new = self._begin(body, "motion_request")
        if not new:
            return receipt
        self.ledger.finish(body["command_id"], {k: v for k, v in body.items() if k != "command_id"})
        if reason:
            return self.ledger.finish(body["command_id"], {"status": "failed", "detail": reason})
        self.last_preset = None
        context = {
            "body": dict(body),
            "profile": self.policy.get("profiles", {}).get(body["action"], {}),
            "stop": asyncio.Event(),
            "phase": "preparing",
            "generation": self._generation(),
            "issued": False,
            "registered": False,
            "reason": "duration_elapsed",
            "stop_ids": [],
            "lease": self.clock() + self.policy["lease_ms"] / 1000,
            "deadline": float("inf"),
            "control_deadline": float("inf"),
            "published": 0,
            "max_gap_ms": 0.0,
        }
        self.active = context
        self.ledger.motion_started(body["command_id"], body["action"], self.wall_clock())
        result = self.ledger.finish(
            body["command_id"],
            {
                "status": "accepted",
                "detail": "已进入受管运动流程，尚未确认实际执行",
                "feedback_source": "bridge-runtime",
                "simulated": False,
            },
        )
        self.worker = asyncio.create_task(self._run(context), name="bounded-t1-motion")
        LOG.info(
            "motion accepted device=%s action=%s command=%s source=%s control_revision=%s",
            self.config.device_uid,
            body["action"],
            body["command_id"],
            body["source"],
            body["control_revision"],
        )
        return result

    async def stop(self, body):
        self.validate(body)
        replay = self._replay(body, "motion_stop")
        if replay is not None:
            return replay
        self._fence(body, stopping=True)
        receipt, new = self._begin(body, "motion_stop")
        if not new:
            return receipt
        self.ledger.finish(body["command_id"], {k: v for k, v in body.items() if k != "command_id"})
        context = self.active
        if context is None:
            if self.policy.get("version") == 2:
                result = await self.stop_preset_t1(body)
                if result is not None:
                    return self.ledger.finish(body["command_id"], result)
            return self.ledger.finish(
                body["command_id"],
                {"status": "failed", "detail": "没有本实例可停止的任务；不能证明机器人已停止"},
            )
        context["stop_ids"].append(body["command_id"])
        context["reason"] = "manual_stop" if body["source"] == "manual" else "model_stop"
        context["stop"].set()
        LOG.info(
            "motion stop requested device=%s command=%s target=%s",
            self.config.device_uid,
            body["command_id"],
            context["body"]["command_id"],
        )
        self.ledger.finish(
            context["body"]["command_id"],
            {"status": "stop_requested", "detail": "停止已请求，等待 SDK 和反馈"},
        )
        return self.ledger.finish(
            body["command_id"],
            {
                "status": "stop_requested",
                "target_command_id": context["body"]["command_id"],
                "detail": "停止已进入独立通路，尚未证明实际停住",
            },
        )

    def heartbeat(self, body):
        self.validate(body)
        self.ledger.fence_motion(body, advance=False)
        context = self.active
        if (
            context is None
            or context["stop"].is_set()
            or any(body.get(key) != context["body"].get(key) for key in SCOPE_FIELDS | {"session_id"})
        ):
            raise BridgeError(409, "运动租约不属于当前设备/连接/会话，或已进入停止流程")
        if self.clock() >= context["lease"] or self.clock() >= context["deadline"]:
            context["reason"] = "lease_expired" if self.clock() >= context["lease"] else "duration_elapsed"
            context["stop"].set()
            raise BridgeError(409, "运动租约已过期，不能通过迟到心跳恢复运动")
        context["lease"] = self.clock() + self.policy["lease_ms"] / 1000
        return {"command_id": body["command_id"], "lease_ms": self.policy["lease_ms"]}

    def _permit(self, context, *, zero=False):
        if self.policy.get("version") == 2:
            return self.permit_t1(context, zero=zero)
        if self.active is not context or not self._source() or self._generation() != context["generation"]:
            return False
        if zero:
            return True
        if any(
            context["body"].get(key) != self.ledger.motion_fence.get(key)
            for key in ("control_revision", "binding_revision", "connection_epoch")
        ):
            return False
        return (
            not context["stop"].is_set()
            and self.clock() < context["lease"]
            and self.clock() < context["deadline"]
            and self.clock() < context["control_deadline"]
        )

    async def _wait(self, operation, context):
        task, stopper = asyncio.create_task(operation), asyncio.create_task(context["stop"].wait())
        try:
            timeout = max(0.0, min(context["lease"], context["deadline"]) - self.clock())
            done, _ = await asyncio.wait(
                (task, stopper), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if stopper in done or not done:
                if not done:
                    context["reason"] = (
                        "lease_expired" if self.clock() >= context["lease"] else "duration_elapsed"
                    )
                    context["stop"].set()
                raise Interrupted()
            result = task.result()
            if not self._permit(context):
                context["reason"] = "feedback_lost"
                raise Interrupted()
            return result
        finally:
            for pending in (task, stopper):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, stopper, return_exceptions=True)

    @staticmethod
    def _accepted(result):
        if result.get("status") != "accepted":
            raise BridgeError(502, "SDK 未明确受理运动步骤，执行状态保留未知")

    async def _run(self, context):
        if self.policy.get("version") == 2:
            return await self.run_t1(context)
        pump = monitor = None
        try:
            if not self._permit(context):
                raise Interrupted()
            profile, spec = context["profile"], ACTIONS[context["body"]["action"]]
            mode = await self._wait(self.adapter.motion_mode(), context)
            if mode != {"mode": profile["mode"], "status": 100, "switch_ready": True}:
                raise BridgeError(409, "SDK 当前模式不符；未自动切换")
            control = await self._wait(self.adapter.motion_control(), context)
            if control["name"] not in self.config.motion["allowed_input_sources"]:
                raise BridgeError(409, "当前控制来源未经允许或正在人工控制")
            context["control_name"] = control["name"]
            if spec["kind"] == "velocity":
                context["registered"] = True
                self._accepted(
                    await self._wait(
                        self.adapter.motion_acquire(
                            self.policy["priority"],
                            self.policy["source_timeout_ms"],
                            permit=lambda: self._permit(context),
                        ),
                        context,
                    )
                )
                await self._wait(
                    self.adapter.motion_velocity((0, 0, 0), permit=lambda: self._permit(context)), context
                )
                control = await self._wait(self.adapter.motion_control(), context)
                if (
                    control["name"] != self.adapter.motion_source
                    or control["priority"] != self.policy["priority"]
                    or control["timeout_ms"] != self.policy["source_timeout_ms"]
                ):
                    raise BridgeError(409, "未取得配置的独立控制源；不发送非零速度")
                context["control_name"] = self.adapter.motion_source
            context["deadline"] = self.clock() + profile["duration_ms"] / 1000
            context["control_deadline"] = self.clock() + max(0.5, self.policy["control_check_ms"] * 3 / 1000)
            context["issued"] = True
            if spec["kind"] == "preset":
                self._accepted(
                    await self._wait(
                        self.adapter.motion_wave(spec["preset"], permit=lambda: self._permit(context)),
                        context,
                    )
                )
            else:
                pump = asyncio.create_task(self._pump(context))
            context["phase"] = "running"
            self.ledger.finish(
                context["body"]["command_id"],
                {
                    "status": "running",
                    "execution_confirmed": False,
                    "detail": "SDK 已受理/开始输出；尚无动作完成证据",
                    "control_source": context["control_name"],
                },
            )
            monitor = asyncio.create_task(self._monitor(context))
            while not context["stop"].is_set():
                if not self._permit(context):
                    context["reason"] = (
                        "lease_expired"
                        if self.clock() >= context["lease"]
                        else "duration_elapsed"
                        if self.clock() >= context["deadline"]
                        else "feedback_lost"
                    )
                    context["stop"].set()
                    break
                await asyncio.sleep(0.01)
        except Interrupted:
            pass
        except asyncio.CancelledError:
            context["reason"] = "shutdown"
        except Exception:
            context["reason"] = "sdk_failure"
        finally:
            context["phase"] = "stopping"
            context["stop"].set()
            for task in (pump, monitor):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (pump, monitor) if t is not None), return_exceptions=True)
            await self._finish(context)

    async def _pump(self, context):
        interval, last, target = 1 / self.policy["publish_hz"], None, self.clock()
        try:
            while self._permit(context):
                current = self.clock()
                if last is not None:
                    gap = (current - last) * 1000
                    context["max_gap_ms"] = max(context["max_gap_ms"], gap)
                    if gap > self.policy["maximum_gap_ms"]:
                        context["reason"] = "publish_gap"
                        break
                await self.adapter.motion_velocity(
                    context["profile"]["velocity"], permit=lambda: self._permit(context)
                )
                context["published"] += 1
                last, target = current, max(target + interval, self.clock())
                await asyncio.sleep(max(0.0, target - self.clock()))
        except asyncio.CancelledError:
            raise
        except Exception:
            context["reason"] = "publisher_failure"
        finally:
            if not context["stop"].is_set():
                context["reason"] = (
                    "lease_expired"
                    if self.clock() >= context["lease"]
                    else "duration_elapsed"
                    if self.clock() >= context["deadline"]
                    else context["reason"]
                )
            context["stop"].set()

    async def _monitor(self, context):
        try:
            while not context["stop"].is_set():
                await asyncio.sleep(self.policy["control_check_ms"] / 1000)
                control = await self._wait(self.adapter.motion_control(), context)
                mode = await self._wait(self.adapter.motion_mode(), context)
                if (
                    control["name"] != context["control_name"]
                    or mode["mode"] != context["profile"]["mode"]
                    or mode["status"] != 100
                ):
                    context["reason"] = "control_lost"
                    break
                if not self._battery_ok():
                    context["reason"] = "feedback_lost"
                    break
                context["control_deadline"] = self.clock() + max(
                    0.5, self.policy["control_check_ms"] * 3 / 1000
                )
        except asyncio.CancelledError:
            raise
        except Interrupted:
            pass
        except Exception:
            context["reason"] = "feedback_lost"
        finally:
            context["stop"].set()

    async def _finish(self, context):
        result = {
            "status": "unknown" if context["issued"] or context["registered"] else "failed",
            "execution_confirmed": False,
            "stop_reason": context["reason"],
            "detail": "受管输出已结束；机器人实际结束状态未确认",
            "feedback_source": "bridge-runtime",
            "published_count": context["published"],
            "maximum_publish_gap_ms": context["max_gap_ms"],
        }
        try:
            if context["registered"]:
                zero_end = self.clock() + self.policy["stop_zero_ms"] / 1000
                while self.clock() < zero_end:
                    await self.adapter.motion_velocity(
                        (0, 0, 0), permit=lambda: self._permit(context, zero=True)
                    )
                    await asyncio.sleep(1 / self.policy["publish_hz"])
                result["zero_velocity_sent"] = True
            stop = context["profile"]["stop"]
            if context["issued"] and stop["mode"] is not None and context["reason"] != "control_lost":
                control = await self.adapter.motion_control()
                if control["name"] != context.get("control_name") or not self._permit(context, zero=True):
                    raise BridgeError(409, "停止前控制来源变化，不向新操作者下发模式转换")
                self._accepted(
                    await self.adapter.motion_stop_mode(
                        stop["mode"], permit=lambda: self._permit(context, zero=True)
                    )
                )
                result["stop_mode_requested"] = stop["mode"]
                mode = await self.adapter.motion_mode()
                if (
                    self._permit(context, zero=True)
                    and stop["feedback"] == "validated_mode"
                    and mode == {"mode": stop["mode"], "status": 100, "switch_ready": True}
                ):
                    result.update(
                        status="stopped",
                        execution_confirmed=True,
                        feedback_source="validated-stop-mode",
                        detail="已观测到本设备核验过的停止模式；不代表原动作自然完成",
                    )
        except (Exception, asyncio.CancelledError):
            result.update(
                status="unknown",
                execution_confirmed=False,
                detail="停止流程或反馈失败；不得据此判断机器人已停止",
            )
        finally:
            if context["registered"]:
                try:
                    self._accepted(
                        await self.adapter.motion_release(
                            self.policy["priority"],
                            self.policy["source_timeout_ms"],
                            permit=lambda: self._permit(context, zero=True),
                        )
                    )
                    result["control_released"] = True
                except Exception:
                    result.update(
                        status="unknown",
                        execution_confirmed=False,
                        control_released=False,
                        detail="控制源释放未确认；结果保持未知，不重放",
                    )
            self.ledger.finish(context["body"]["command_id"], result)
            LOG.info(
                "motion ended device=%s command=%s status=%s reason=%s published=%s",
                self.config.device_uid,
                context["body"]["command_id"],
                result["status"],
                context["reason"],
                context["published"],
            )
            for identifier in context["stop_ids"]:
                self.ledger.finish(identifier, {**result, "target_command_id": context["body"]["command_id"]})
            if self.active is context:
                self.active = None

    async def close(self):
        self.closing = True
        if self.active is not None:
            self.active["reason"] = "shutdown"
            self.active["stop"].set()
        if self.worker is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.worker), 12)
            except asyncio.TimeoutError:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
