"""Built-in T1 profiles and compatibility for explicitly configured legacy profiles."""

from copy import deepcopy
import math

from t1_lab._vendor.contracts.catalog import COMMITS
from t1_lab._vendor.adapters.ros.motion_protocol import STOP_MODES
from t1_lab._vendor.families.t1 import PRESETS
from t1_lab._vendor.families.t1.motion import actions

ACTIONS = actions(PRESETS)
UNVERIFIED = "运动功能已启用，但尚未填写此设备的 motion.runtime（动作参数、停止方式与控制源配置）"


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(name + " 字段必须为：" + ", ".join(sorted(expected)))


def _number(value, low, high, name, *, integer=False):
    types = (int,) if integer else (int, float)
    if type(value) not in types or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(name + " 超出本程序允许范围")


def _note(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(name + " 必须有对应设备的核验记录，不能留空")


def runtime_settings(value):
    if isinstance(value, dict) and (not value or value.get("version") == 2):
        defaults = {
            "version": 2,
            "priority": 80,
            "source_timeout_ms": 1000,
            "publish_hz": 50,
            "maximum_gap_ms": 200,
            "control_check_ms": 200,
            "lease_ms": 2000,
            "stop_zero_ms": 200,
            "move_duration_ms": 800,
            "linear_speed": 0.15,
            "angular_speed": 0.3,
            "transition_timeout_ms": 20000,
        }
        if set(value) - defaults.keys():
            raise ValueError("motion.runtime v2 含未知配置")
        result = {**defaults, **value}
        for key, low, high in (
            ("priority", 0, 100),
            ("source_timeout_ms", 500, 2000),
            ("publish_hz", 50, 100),
            ("maximum_gap_ms", 30, 500),
            ("control_check_ms", 50, 500),
            ("lease_ms", 1000, 2000),
            ("stop_zero_ms", 100, 1000),
            ("move_duration_ms", 100, 5000),
            ("transition_timeout_ms", 5000, 30000),
        ):
            _number(result[key], low, high, key, integer=True)
        _number(result["linear_speed"], 0.05, 0.5, "linear_speed")
        _number(result["angular_speed"], 0.1, 0.8, "angular_speed")
        if result["maximum_gap_ms"] >= result["source_timeout_ms"]:
            raise ValueError("发布间隔必须短于控制源超时")
        return result
    expected = {
        "device_uid",
        "sdk_commit",
        "firmware",
        "verification_note",
        "watchdog_verification_note",
        "priority",
        "source_timeout_ms",
        "publish_hz",
        "maximum_gap_ms",
        "control_check_ms",
        "lease_ms",
        "stop_zero_ms",
        "profiles",
    }
    _keys(value, expected, "motion.runtime")
    for key in ("device_uid", "firmware", "verification_note", "watchdog_verification_note"):
        _note(value[key], "motion.runtime." + key)
    if value["sdk_commit"] != COMMITS["t1"]:
        raise ValueError("运动核验记录必须对应固定 T1 SDK")
    for key, low, high in (
        ("priority", 0, 59),
        ("source_timeout_ms", 100, 2000),
        ("publish_hz", 50, 100),
        ("maximum_gap_ms", 30, 200),
        ("control_check_ms", 50, 500),
        ("lease_ms", 250, 2000),
        ("stop_zero_ms", 100, 2000),
    ):
        _number(value[key], low, high, "motion.runtime." + key, integer=True)
    if value["maximum_gap_ms"] >= value["source_timeout_ms"]:
        raise ValueError("发布间隔阈值必须短于控制源超时")
    if value["control_check_ms"] >= value["lease_ms"]:
        raise ValueError("控制反馈检查周期必须短于主服务租约")
    profiles = value["profiles"]
    if not isinstance(profiles, dict) or not profiles or set(profiles) - ACTIONS.keys():
        raise ValueError("profiles 只能登记有限动作名，且不能为空")
    for name, profile in profiles.items():
        _keys(profile, {"mode", "duration_ms", "velocity", "verification_note", "stop"}, "profile " + name)
        spec = ACTIONS[name]
        if spec["kind"] == "mode":
            raise ValueError("姿态转换使用 runtime version=2，不使用旧速度配置")
        allowed_modes = {spec["mode"]} if spec["kind"] == "preset" else {102, 202, 300}
        if type(profile["mode"]) is not int or profile["mode"] not in allowed_modes:
            raise ValueError(name + " 模式不在已实现的候选范围")
        _number(profile["duration_ms"], 100, 5000, name + ".duration_ms", integer=True)
        _note(profile["verification_note"], name + ".verification_note")
        velocity = profile["velocity"]
        if not isinstance(velocity, list) or len(velocity) != 3:
            raise ValueError(name + ".velocity 须为三个有限数值")
        for item in velocity:
            _number(item, -2, 2, name + ".velocity")
        if spec["kind"] == "preset":
            if any(velocity):
                raise ValueError("挥手不能同时发布行走速度")
        elif velocity[spec["axis"]] * spec["sign"] <= 0 or any(
            item != 0 for axis, item in enumerate(velocity) if axis != spec["axis"]
        ):
            raise ValueError(name + " 只能使用其固定方向；速度大小仍须实机验证")
        stop = profile["stop"]
        _keys(stop, {"mode", "verification_note", "feedback"}, name + ".stop")
        _note(stop["verification_note"], name + ".stop.verification_note")
        if stop["mode"] is not None and (type(stop["mode"]) is not int or stop["mode"] not in STOP_MODES):
            raise ValueError("停止模式未在实现的候选内")
        compatible = {102: {1, 2, 101}, 202: {1, 2, 201}, 300: {1, 2, 201, 300}}
        if stop["mode"] is not None and stop["mode"] not in compatible[profile["mode"]]:
            raise ValueError("停止策略不能跨双足/四足形态")
        if stop["feedback"] not in ("operator", "validated_mode"):
            raise ValueError("停止反馈须为 operator 或经本固件验证的 validated_mode")
        if (spec["kind"] == "preset" or stop["feedback"] == "validated_mode") and stop["mode"] is None:
            raise ValueError("挥手中止/模式反馈需要明确的已验证停止模式，零速度不能替代")
        if stop["feedback"] == "validated_mode" and stop["mode"] == profile["mode"]:
            raise ValueError("模式未变化不能证明原动作已停止；须使用人工观察或不同的核验停止模式")
    return deepcopy(value)
