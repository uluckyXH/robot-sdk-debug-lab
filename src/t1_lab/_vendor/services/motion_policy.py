"""Finite SDK candidates and pure, fail-closed checks; no ROS or network imports."""

from datetime import datetime, timezone
import math

from t1_lab._vendor.contracts.catalog import BridgeError, COMMITS

STOP_REASON = "未验证当前固件的独立停止/中断语义；不把 PASSIVE、DAMPING 或 interrupt 当作已验证停止"
SERVICE = "/aimdk_5Fmsgs/srv/SetMcPresetMotion"
from t1_lab._vendor.families import MODES, PRESETS

INPUT_SOURCES = frozenset(("simulator", "app_proxy", "interaction", "pnc"))
# Only discharge and full flags are permitted by this conservative experiment gate.
# This is not a manufacturer safety specification or an authorization for hardware.
ALLOWED_BMS_BITS = (1 << 2) | (1 << 20)
SETTING_KEYS = frozenset(
    (
        "enabled",
        "allowed_presets",
        "allowed_input_sources",
        "minimum_interval_seconds",
        "minimum_battery_percent",
        "runtime",
    )
)
REQUIREMENTS = [
    "固定 SDK 与目标固件、整机身份和网络隔离的实机核验记录",
    "厂商确认的停止/取消接口、响应与实际停止状态的对应关系",
    "在实际姿态、负载和故障条件下验证停止延迟、掉线行为及人工接管",
    "逐预设验证动作编号、关节范围、姿态条件、完成反馈和物理活动空间",
    "实现并测试真实执行与停止适配器，再进行代码审查；配置标志不能代替这些工作",
]


def settings(family, value):
    if not isinstance(value, dict) or set(value) - SETTING_KEYS:
        raise ValueError("motion 含未知配置；不支持 stop_verified 或开放任意动作")
    result = {
        "enabled": True,
        "allowed_presets": list(PRESETS[family]),
        "allowed_input_sources": sorted(INPUT_SOURCES),
        "minimum_interval_seconds": 10,
        "minimum_battery_percent": 30,
        "runtime": {},
        **value,
    }
    if type(result["enabled"]) is not bool:
        raise ValueError("motion.enabled 须为布尔值")
    for key, allowed in (("allowed_presets", PRESETS[family]), ("allowed_input_sources", INPUT_SOURCES)):
        values = result[key]
        if not isinstance(values, (list, tuple)) or any(
            not isinstance(x, str) or x not in allowed for x in values
        ):
            raise ValueError("motion." + key + " 须在当前型号白名单内；rc/vr 不可授权")
        if len(values) != len(set(values)):
            raise ValueError("motion." + key + " 不得重复")
        result[key] = list(values)
    for key, low, high in (("minimum_interval_seconds", 10, 300), ("minimum_battery_percent", 30, 100)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError("motion." + key + " 超出允许范围")
    from t1_lab._vendor.services.motion_runtime_policy import runtime_settings

    result["runtime"] = runtime_settings(result["runtime"]) if family == "t1" or result["runtime"] else {}
    return result


def preset_info(family, name):
    if not isinstance(name, str) or name not in PRESETS[family]:
        raise BridgeError(422, "预设不在当前型号的有限登记表中")
    return {
        "name": name,
        **PRESETS[family][name],
        "hardware_verified": False,
        "sdk_commit": COMMITS[family],
        "required_mc_action": MODES[family],
    }


def plan(family, name):
    preset = preset_info(family, name)
    return {
        "preset": preset,
        "service": SERVICE,
        "source": "sdk_rpc",
        "area": 0,
        "motion": preset["id"],
        "interrupt": False,
        "ani_path": "",
        "automatic_mode_switch": False,
        "completion_observable": False,
        "warning": "area 不约束实际关节；动作范围/持续时间/停止仍未验证",
    }


def populate_request(request, family, name, stamp):
    value = plan(family, name)
    request.header.stamp = stamp
    request.source = value["source"]
    request.area.value = value["area"]
    request.motion.value = value["motion"]
    request.interrupt = False
    request.ani_path = ""


def response_result(response):
    """Parse an offline/fake SDK reply without claiming completion or safe stop."""
    task = getattr(response, "response", None)
    code = getattr(getattr(task, "header", None), "code", None)
    state = getattr(getattr(task, "state", None), "value", None)
    task_id = getattr(task, "task_id", None)
    valid = type(code) is int and type(state) is int and type(task_id) is int and 0 <= task_id < 2**64
    if not valid:
        return {
            "status": "unknown",
            "execution_confirmed": False,
            "feedback_source": "sdk-response",
            "detail": "动作响应格式无效；执行状态未知",
        }
    if code != 0 or state in (2, 3, 4, 5, 6, 100):
        status = "failed"
    elif state in (0, 1, 200, 300, 400):
        status = "accepted"
    else:
        status = "unknown"
    return {
        "status": status,
        "execution_confirmed": False,
        "feedback_source": "sdk-response",
        "sdk_code": code,
        "sdk_state": state,
        "sdk_task_id": task_id,
        "detail": "仅解析 SDK 回执；task_id/RUNNING 不证明动作完成",
    }


def _field(snapshot, group, key, current_time, maximum_age):
    telemetry = snapshot.get("telemetry")
    rows = telemetry.get(group) if isinstance(telemetry, dict) else None
    if not isinstance(rows, list) or len(rows) > 256:
        return None
    matches = [row for row in rows if isinstance(row, dict) and row.get("key") == key]
    if len(matches) != 1:
        return None
    row = matches[0]
    try:
        stamp = row["observed_at"]
        # Python 3.10 does not accept the RFC 3339 UTC suffix that 3.11+ accepts.
        if isinstance(stamp, str) and stamp.endswith("Z"):
            stamp = stamp[:-1] + "+00:00"
        observed = datetime.fromisoformat(stamp)
        age = (current_time - observed).total_seconds()
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (
        row.get("availability") != "fresh"
        or not isinstance(row.get("source"), str)
        or not row["source"]
        or not 0 <= age <= maximum_age
    ):
        return None
    return row


def evaluate(config, snapshot, name, *, current_time=None):
    """A saved snapshot can explain gates, never establish hardware verification."""
    preset_info(config.family, name)
    if not isinstance(snapshot, dict):
        raise BridgeError(422, "快照须为 /bridge/v1/state 结构的对象")
    policy = settings(config.family, config.motion)
    current_time = current_time or datetime.now(timezone.utc)
    checks = []

    def check(key, passed, reason):
        checks.append({"key": key, "passed": bool(passed), "reason": reason})

    check("enabled", policy["enabled"], "需要独立 motion.enabled 开关")
    check("allowlisted", name in policy["allowed_presets"], "预设须在当前配置的 allowed_presets 中")
    check(
        "identity",
        all(
            snapshot.get(k) == v
            for k, v in (
                ("device_uid", config.device_uid),
                ("bridge_id", config.bridge_id),
                ("family", config.family),
                ("sdk_commit", COMMITS[config.family]),
            )
        )
        and snapshot.get("simulated") is config.simulated,
        "快照设备、桥接、型号、SDK 和模拟标记必须一致",
    )
    check(
        "source",
        snapshot.get("source_verified") is True
        and type(snapshot.get("source_count")) is int
        and snapshot["source_count"] == 1,
        "需要当前唯一来源证据",
    )
    fingerprint = {}

    def read(group, key):
        row = _field(snapshot, group, key, current_time, config.stale_after)
        fingerprint[key] = (
            None if row is None else {k: row.get(k) for k in ("value", "source", "scale_verified")}
        )
        return row.get("value") if row else None

    action, state = read("motion", "mc_action"), read("motion", "mc_action_status")
    check(
        "mode",
        type(action) is int and action == MODES[config.family] and type(state) is int and state == 100,
        "必须已经处于本型号所需模式且 RUNNING；不自动切换模式，不据此判完成",
    )
    check("switch_ready", read("motion", "mc_switch_ready") is True, "需要新鲜的 switch_ready=true")
    source = read("interaction", "control_input_source")
    check(
        "input_source",
        isinstance(source, str) and source in policy["allowed_input_sources"],
        "当前输入源须显式登记；不争抢 rc/vr 或提升控制优先级",
    )
    charge = read("battery", "remaining_capacity_percentage")
    check(
        "battery_charge",
        type(charge) in (int, float)
        and math.isfinite(charge)
        and policy["minimum_battery_percent"] <= charge <= 100
        and fingerprint["remaining_capacity_percentage"]["scale_verified"] is True,
        "需要新鲜且比例已核验的电量，最低阈值为实验门禁而非厂商安全保证",
    )
    bits, power = read("battery", "status"), read("battery", "power_request")
    check(
        "battery_status",
        type(bits) is int
        and 0 <= bits < 2**32
        and bits & ~ALLOWED_BMS_BITS == 0
        and type(power) is int
        and power == 0,
        "拒绝充电、故障、关机、保留/未知状态位或未知电池状态",
    )
    supported = config.simulated and snapshot.get("source_method") == "simulator"
    check("execution_supported", supported, "当前只支持明确模拟；真实动作始终禁用。" + STOP_REASON)
    return {
        "ready": all(item["passed"] for item in checks),
        "checks": checks,
        "execution_supported": supported,
        "plan": plan(config.family, name),
        "fingerprint": {
            "fields": fingerprint,
            "source_method": snapshot.get("source_method"),
            "source_generation": snapshot.get("source_generation"),
        },
    }
