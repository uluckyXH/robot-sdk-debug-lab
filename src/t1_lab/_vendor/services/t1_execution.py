"""Execution of named T1 actions, only after an explicit motion request."""

import asyncio

from t1_lab._vendor.contracts.catalog import BridgeError
from t1_lab._vendor.families.t1.motion import ARRIVAL, MODE_NAMES, transition_path


class T1Execution:
    def reason_t1(self, name):
        from t1_lab._vendor.services.motion_runtime_policy import ACTIONS

        if not self.supported():
            return "当前执行器不是真实 T1 SDK"
        if not self.config.motion["enabled"]:
            return "运动已在设备配置中关闭"
        if self.closing:
            return "SDK 正在退出"
        if self.active or self.ledger.motion_pending:
            return "已有运动或未决任务；可先使用停止按钮核对"
        spec = ACTIONS[name]
        if spec["kind"] == "preset" and spec["preset"] not in self.config.motion["allowed_presets"]:
            return "此预设已在设备配置中关闭"
        if not self.adapter.motion_available(spec["kind"], None):
            return (
                self.adapter.motion_unavailable_reason(spec["kind"], None)
                or "SDK 运动服务正在重新发现，请刷新运动状态"
            )
        return None

    def permit_t1(self, context, *, zero=False):
        if self.active is not context or self._generation() != context["generation"]:
            return False
        if zero:
            return True
        fence = self.ledger.motion_fence or {}
        if any(
            context["body"].get(key) != fence.get(key)
            for key in ("control_revision", "binding_revision", "connection_epoch")
        ):
            return False
        return (
            not context["stop"].is_set()
            and self.clock() < context["lease"]
            and self.clock() < context["deadline"]
            and self.clock() < context["control_deadline"]
        )

    async def _mode_t1(self, context, target):
        current = await self._wait(self.adapter.motion_mode(), context)
        while current["mode"] in ARRIVAL:
            await asyncio.sleep(0.1)
            current = await self._wait(self.adapter.motion_mode(), context)
        if current["status"] != 100:
            raise BridgeError(409, f"机器人当前模式尚未稳定：{current}")
        for command, destination in transition_path(current["mode"], target):
            control = await self._wait(self.adapter.motion_control(), context)
            if control["name"] != context["control_name"]:
                raise BridgeError(409, "模式切换前控制来源已变化")
            # The vendor switcher checks the RPC result and target-mode readback.
            # T1 can report switch_ready=False even for an accepted transition;
            # retain that diagnostic field without treating it as a veto.
            context["issued"] = True
            reply = await self._wait(
                self.adapter.motion_stop_mode(command, permit=lambda: self._permit(context)), context
            )
            context["steps"].append(
                {
                    "interface": "SetMcAction",
                    "mode": command,
                    "action_desc": MODE_NAMES[command],
                    "response": reply,
                }
            )
            self._accepted(reply)
            while True:
                current = await self._wait(self.adapter.motion_mode(), context)
                if current["mode"] == destination and current["status"] == 100:
                    break
                await asyncio.sleep(0.1)
            context["last_mode"] = current
            await asyncio.sleep(0.5)
        if current["mode"] != target or current["status"] != 100:
            raise BridgeError(409, "模式转换没有到达目标")
        context["last_mode"] = current
        return current

    async def run_t1(self, context):
        from t1_lab._vendor.services.motion_runtime_policy import ACTIONS

        spec = ACTIONS[context["body"]["action"]]
        context.update(steps=[], output_finished=False, control_name="", error=None)
        try:
            mode = await self._wait(self.adapter.motion_mode(), context)
            control = await self._wait(self.adapter.motion_control(), context)
            context["control_name"] = control["name"]
            # Never take control away from an active RC/VR or another SDK client.
            if control["name"] not in (
                *self.config.motion["allowed_input_sources"],
                "",
                self.adapter.motion_source,
            ):
                raise BridgeError(409, "当前控制来源正在占用机器人：" + control["name"])
            target = spec.get("mode", 300 if mode["mode"] >= 200 else 102)
            context["deadline"] = self.clock() + self.policy["transition_timeout_ms"] / 1000
            await self._mode_t1(context, target)
            if spec["kind"] == "mode":
                context["output_finished"] = True
                context["result"] = {
                    "status": "completed",
                    "execution_confirmed": True,
                    "feedback_source": "GetMcAction",
                    "mode": context["last_mode"],
                    "detail": "厂家已读回目标运动模式；不代表其他动作已执行",
                }
                return
            if spec["kind"] == "preset":
                context["issued"] = True
                reply = await self._wait(
                    self.adapter.motion_wave(spec["preset"], permit=lambda: self._permit(context)), context
                )
                context["steps"].append(
                    {"interface": "SetMcPresetMotion", "preset": spec["preset"], "response": reply}
                )
                self._accepted(reply)
                # The RPC has no reliable animation-completion event. Release the
                # request slot, retain accepted semantics, and never invent one.
                context["output_finished"] = True
                self.last_preset = {"body": dict(context["body"]), "generation": context["generation"]}
                context["result"] = {
                    **reply,
                    "status": "accepted",
                    "execution_confirmed": False,
                    "detail": "厂家已受理预设动作，可继续操作；实际动画完成未确认",
                }
                return
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
            await asyncio.sleep(0.05)
            control = await self._wait(self.adapter.motion_control(), context)
            if control["name"] != self.adapter.motion_source:
                raise BridgeError(409, "未取得本程序的控制源；未发送非零速度")
            context["control_name"] = self.adapter.motion_source
            velocity = [0.0, 0.0, 0.0]
            velocity[spec["axis"]] = (
                spec["sign"] * self.policy["angular_speed" if spec["axis"] == 2 else "linear_speed"]
            )
            context["deadline"] = self.clock() + self.policy["move_duration_ms"] / 1000
            context["issued"], context["phase"] = True, "running"
            self.ledger.finish(
                context["body"]["command_id"],
                {
                    "status": "running",
                    "detail": "正在发布限时速度",
                    "control_source": self.adapter.motion_source,
                },
            )
            interval, last, check_at = 1 / self.policy["publish_hz"], None, 0.0
            while self._permit(context):
                stamp = self.clock()
                if last is not None:
                    gap = (stamp - last) * 1000
                    context["max_gap_ms"] = max(context["max_gap_ms"], gap)
                    if gap > self.policy["maximum_gap_ms"]:
                        raise BridgeError(409, "速度发布间隔超出预算，已进入停止流程")
                if stamp >= check_at:
                    control = await self._wait(self.adapter.motion_control(), context)
                    current = await self._wait(self.adapter.motion_mode(), context)
                    if (
                        control["name"] != self.adapter.motion_source
                        or current["mode"] != target
                        or current["status"] != 100
                    ):
                        context["reason"] = "control_lost"
                        raise BridgeError(409, "控制来源或运动模式已改变，已停止非零输出")
                    check_at = self.clock() + self.policy["control_check_ms"] / 1000
                await self._wait(
                    self.adapter.motion_velocity(velocity, permit=lambda: self._permit(context)), context
                )
                context["published"] += 1
                last = stamp
                await asyncio.sleep(interval)
            context["reason"] = (
                "lease_expired"
                if self.clock() >= context["lease"]
                else context["reason"]
                if context["stop"].is_set()
                else "duration_elapsed"
            )
        except asyncio.CancelledError:
            context["reason"] = "shutdown"
        except Exception as exc:
            context["error"] = str(getattr(exc, "detail", exc))[:600]
        finally:
            context["phase"] = "stopping"
            await self.finish_t1(context, spec)

    async def finish_t1(self, context, spec):
        result = context.get("result") or {
            "status": "unknown" if context["issued"] else "failed",
            "execution_confirmed": False,
            "detail": context.get("error") or "请求已终止，执行反馈未确认",
        }
        try:
            if context["registered"]:
                end = self.clock() + self.policy["stop_zero_ms"] / 1000
                while self.clock() < end:
                    await self.adapter.motion_velocity(
                        (0, 0, 0), permit=lambda: self._permit(context, zero=True)
                    )
                    await asyncio.sleep(1 / self.policy["publish_hz"])
                self._accepted(
                    await self.adapter.motion_release(
                        self.policy["priority"],
                        self.policy["source_timeout_ms"],
                        permit=lambda: self._permit(context, zero=True),
                    )
                )
                context["output_finished"] = True
                result.update(
                    status="failed" if context.get("error") else "stopped",
                    execution_confirmed=False,
                    control_released=True,
                    zero_velocity_sent=True,
                    feedback_source="SDK-zero-and-source-release",
                    detail=context.get("error") or "零速度已发送并释放控制源；物理停止请结合实时画面确认",
                )
            elif context["stop"].is_set() and context["issued"] and not context["output_finished"]:
                # Finish an in-flight posture change by querying it, never blindly
                # force passive/damping and risk dropping a standing robot.
                current = await self.adapter.motion_mode()
                if current["status"] == 100:
                    context["output_finished"] = True
                    result.update(
                        status="stopped",
                        execution_confirmed=False,
                        mode=current,
                        detail="已停止后续模式派发，当前模式已读回；不会重放剩余步骤",
                    )
        except Exception as exc:
            context["output_finished"] = False
            result.update(
                status="unknown",
                execution_confirmed=False,
                detail="停止或释放失败：" + str(getattr(exc, "detail", exc))[:400],
            )
        result.update(
            output_finished=context["output_finished"],
            steps=context["steps"],
            stop_reason=context["reason"],
            published_count=context["published"],
            maximum_publish_gap_ms=context["max_gap_ms"],
        )
        self.ledger.finish(context["body"]["command_id"], result)
        for identifier in context["stop_ids"]:
            self.ledger.finish(identifier, {**result, "target_command_id": context["body"]["command_id"]})
        if self.active is context:
            self.active = None

    async def stop_preset_t1(self, body):
        previous = self.last_preset
        if not previous or any(
            body[key] != previous["body"][key]
            for key in ("device_uid", "binding_revision", "connection_epoch")
        ):
            return None

        def permit():
            fence = self.ledger.motion_fence or {}
            return (
                not self.closing
                and self.active is None
                and self._generation() == previous["generation"]
                and all(
                    body[key] == fence.get(key)
                    for key in ("binding_revision", "connection_epoch", "control_revision")
                )
            )

        try:
            mode = await self.adapter.motion_mode()
            control = await self.adapter.motion_control()
            if (
                mode["mode"] != 300
                or mode["status"] != 100
                or control["name"]
                not in (*self.config.motion["allowed_input_sources"], "", self.adapter.motion_source)
            ):
                raise BridgeError(409, "当前姿态或控制来源已变化，未派发预设中止")
            if not permit():
                raise BridgeError(409, "连接或控制代次已变化")
            reply = await self.adapter.motion_wave("t1.default_pose", permit=permit)
            self._accepted(reply)
            self.last_preset = None
            return {
                **reply,
                "status": "accepted",
                "output_finished": True,
                "execution_confirmed": False,
                "target_command_id": previous["body"]["command_id"],
                "detail": "已发送默认姿态并中断预设；厂家已受理，实际停止请结合画面确认",
            }
        except Exception as exc:
            return {
                "status": "failed",
                "execution_confirmed": False,
                "detail": str(getattr(exc, "detail", exc))[:600],
            }
