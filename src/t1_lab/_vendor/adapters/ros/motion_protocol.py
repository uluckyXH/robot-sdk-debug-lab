"""Pinned T1 movement messages; these helpers neither authorize nor send motion."""

import math
import re

from t1_lab._vendor.contracts.catalog import BridgeError
from t1_lab._vendor.services.motion_policy import populate_request as preset_request, response_result
from t1_lab._vendor.families.t1.motion import MODE_NAMES

MOTION_SERVICES = {
    "motion_preset": ("/aimdk_5Fmsgs/srv/SetMcPresetMotion", "SetMcPresetMotion"),
    "motion_input": ("/aimdk_5Fmsgs/srv/SetMcInputSource", "SetMcInputSource"),
    "motion_mode": ("/aimdk_5Fmsgs/srv/SetMcAction", "SetMcAction"),
}
VELOCITY_TOPIC = "/aima/mc/locomotion/velocity"
OWN_SOURCE = re.compile(r"roboweave_[a-f0-9]{24}\Z")
INPUT_ACTIONS = {"add": 1001, "delete": 1003}
# Legacy stop modes; v2 normal transitions use the pinned T1 catalog.
STOP_MODES = frozenset((1, 2, 101, 201, 300))


def populate_motion_request(kind, request, args, stamp):
    if kind == "motion_preset":
        preset_request(request, "t1", args["preset"], stamp)
        if "interrupt" in args:
            if type(args["interrupt"]) is not bool:
                raise BridgeError(422, "interrupt 必须为布尔值")
            request.interrupt = args["interrupt"]
    elif kind == "motion_input":
        if args["operation"] not in INPUT_ACTIONS or not OWN_SOURCE.fullmatch(args["source"]):
            raise BridgeError(422, "只能添加或删除当前 RoboWeave 实例的独立控制源")
        if type(args["priority"]) is not int or not 0 <= args["priority"] <= 100:
            raise BridgeError(422, "控制源优先级须为 0–100")
        if type(args["timeout_ms"]) is not int or not 100 <= args["timeout_ms"] <= 2000:
            raise BridgeError(422, "控制源超时须为 100–2000 ms")
        request.request.header.stamp = stamp
        request.action.value = INPUT_ACTIONS[args["operation"]]
        request.input_source.name = args["source"]
        request.input_source.priority = args["priority"]
        request.input_source.timeout = args["timeout_ms"]
    elif kind == "motion_mode":
        if type(args["mode"]) is not int or args["mode"] not in MODE_NAMES:
            raise BridgeError(422, "此模式未登记为 T1 SDK 模式")
        request.header.stamp = stamp
        request.source = "sdk_rpc"
        request.command.action.value = args["mode"]
        request.command.action_desc = MODE_NAMES[args["mode"]]
    else:
        raise BridgeError(422, "未知运动服务")
    return request


def populate_velocity(message, source, velocity, stamp, sequence):
    if not isinstance(source, str) or not OWN_SOURCE.fullmatch(source):
        raise BridgeError(422, "速度输出必须使用本实例的独立控制源")
    if len(velocity) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in velocity):
        raise BridgeError(422, "速度须为有限三元组")
    message.header.stamp = stamp
    message.header.sequence = sequence % (2**32)
    message.source = source
    message.forward_velocity, message.lateral_velocity, message.angular_velocity = map(float, velocity)
    message.pitch_velocity, message.level = 0.0, 0.0
    return message


def mode_result(response):
    info = getattr(response, "info", None)
    code = getattr(getattr(response, "header", None), "code", None)
    mode = getattr(getattr(info, "current_action", None), "value", None)
    status = getattr(getattr(info, "status", None), "value", None)
    ready = getattr(info, "switch_ready", None)
    if (
        type(code) is not int
        or code != 0
        or type(mode) is not int
        or type(status) is not int
        or type(ready) is not bool
    ):
        raise BridgeError(502, "运动模式反馈无效")
    return {"mode": mode, "status": status, "switch_ready": ready}


def input_result(response):
    result = response_result(response)
    item = getattr(response, "input_source", None)
    name = getattr(item, "name", None)
    priority, timeout = getattr(item, "priority", None), getattr(item, "timeout", None)
    if (
        result["status"] != "accepted"
        or not isinstance(name, str)
        or len(name) > 128
        or type(priority) is not int
        or type(timeout) is not int
    ):
        raise BridgeError(502, "控制源反馈无效或被拒绝")
    return {"name": name, "priority": priority, "timeout_ms": timeout}
