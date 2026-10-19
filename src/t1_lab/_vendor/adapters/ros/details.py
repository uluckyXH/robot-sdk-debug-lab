"""Read-only SDK detail mapping and cache. Getter success is not motion completion."""

from copy import deepcopy
import json
from t1_lab._vendor.resources import resource_file
import threading
import time

from t1_lab._vendor.contracts.catalog import BridgeError, READ_SERVICES, now
from t1_lab._vendor.adapters.ros.protocol import common_result

ACTION_NAMES = json.loads(resource_file("action-names.json").read_text())
STATUS_NAMES = {0: "IDLE/未知（不代表物理空闲或停止）", 100: "RUNNING", 200: "TRANSITION"}
BMS_TEXT = (
    ("manufacturer", "manufacturer", "电池厂家"),
    ("serial_number", "battery_serial_number", "电池序列号（不是整机 SN）"),
    ("protocol_version", "battery_protocol_version", "电池协议版本"),
    ("hardware_version", "battery_hardware_version", "电池硬件版本"),
    ("software_version", "battery_software_version", "电池软件版本"),
)
BMS_BITS = {
    0: "充电",
    1: "充电过流",
    2: "放电",
    3: "放电过流",
    4: "电池短路",
    5: "电芯过压",
    6: "电芯欠压",
    7: "电池总电压过压",
    8: "电池总电压欠压",
    10: "温感传感器开路",
    11: "放电高温",
    12: "充电高温",
    13: "放电低温",
    14: "充电低温",
    15: "电芯压差超限",
    16: "故障禁止充电",
    17: "故障禁止放电",
    18: "MOSFET过温",
    19: "均衡线电阻超限",
    20: "电池充满",
    21: "新电池固件标志",
    22: "电池座Key断开",
    23: "电池包异常",
    25: "有线充电器Key短接",
    26: "无线充电器接入",
    27: "正常关机触发",
    28: "长按关机触发",
}
RESERVED_MASK = sum(1 << bit for bit in (9, 24, 29, 30, 31))


def text_value(value, maximum=256, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        return None
    return value if value or empty else None


def integer(value, minimum=0, maximum=2**31 - 1):
    return value if type(value) is int and minimum <= value <= maximum else None


def task_result(response):
    code = getattr(getattr(response, "header", None), "code", None)
    state = getattr(getattr(response, "state", None), "value", None)
    return type(code) is int and code == 0 and type(state) is int and state in (0, 1)


def battery_details(message, family, make_field):
    source = "/aima/hal/bms/state"
    fields = [
        make_field(key, label, text_value(getattr(message, attribute, None)), source=source)
        for attribute, key, label in BMS_TEXT
    ]
    bits = integer(getattr(message, "status", None), maximum=2**32 - 1)
    for bit, label in BMS_BITS.items():
        item = make_field(
            "status_bit_" + str(bit),
            label,
            bool(bits & (1 << bit)) if bits is not None else None,
            source=source,
        )
        if family == "t1" and bit in (13, 14):
            item["reason"] = "T1 SDK低温位注释的阈值文字存在歧义；只解释标志，不推测温度阈值"
        fields.append(item)
    fields.append(
        make_field(
            "status_reserved_bits",
            "未解释的保留状态位掩码",
            bits & RESERVED_MASK if bits is not None else None,
            source=source,
        )
    )
    fields.append(
        make_field(
            "power_request",
            "电池关机请求编号",
            integer(getattr(message, "power_request", None), maximum=65535),
            source=source,
        )
    )
    return fields


DETAILS = {
    "get_mc_action": (
        "motion",
        (
            ("mc_action", "运动模式编号", None),
            ("mc_action_label", "SDK模式名称", None),
            ("mc_action_source", "模式触发来源", None),
            ("mc_action_description", "SDK模式描述", None),
            ("mc_action_status", "运动模式阶段编号", None),
            ("mc_action_status_label", "运动模式阶段", None),
            ("mc_switch_ready", "SDK模式切换就绪", None),
        ),
    ),
    "get_input_source": (
        "interaction",
        (
            ("control_input_source", "控制输入源（不代表持续活跃）", None),
            ("control_input_priority", "输入源优先级", None),
            ("control_input_timeout_ms", "输入源超时配置", "ms"),
        ),
    ),
    "get_volume": ("audio", (("volume", "播放音量读回", "%"),)),
    "get_mute": ("audio", (("muted", "静音状态读回", None),)),
}


def read_fields(kind, response, family, make_field):
    if kind == "get_mc_action":
        code = getattr(getattr(response, "header", None), "code", None)
        if type(code) is not int or code != 0:
            raise BridgeError(502, "运动模式查询失败")
        info = response.info
        action = integer(info.current_action.value)
        status = integer(info.status.value)
        values = [
            action,
            ACTION_NAMES[family].get(str(action)),
            text_value(info.source, 128, empty=True),
            text_value(info.action_desc),
            status,
            STATUS_NAMES.get(status),
            info.switch_ready if type(info.switch_ready) is bool else None,
        ]
    elif kind == "get_input_source":
        if not task_result(response.response):
            raise BridgeError(502, "控制输入源查询失败或状态冲突")
        item = response.input_source
        values = [
            text_value(item.name, 128, empty=True),
            integer(item.priority, maximum=100),
            integer(item.timeout),
        ]
    else:
        if not common_result(response.response)[0]:
            raise BridgeError(502, "音频状态查询失败或状态冲突")
        values = (
            [integer(response.audio_volume, maximum=100)]
            if kind == "get_volume"
            else [response.is_mute if type(response.is_mute) is bool else None]
        )
    return [
        make_field(key, label, value, unit, READ_SERVICES[kind][0])
        for (key, label, unit), value in zip(DETAILS[kind][1], values)
    ]


class DetailCache:
    def __init__(self, config, make_field, clock=time.monotonic):
        self.config, self.make_field, self.clock = config, make_field, clock
        self.lock = threading.RLock()
        self.values, self.errors = {}, {}

    def record(self, kind, fields, evidence):
        with self.lock:
            stamp = now()
            for item in fields:
                item["observed_at"] = stamp
            self.values[kind] = (self.clock(), fields, evidence)
            self.errors.pop(kind, None)

    def failed(self, kind, reason):
        with self.lock:
            self.errors[kind] = reason

    def _fresh(self, kind, generation, providers):
        sample = self.values.get(kind)
        current = (generation, providers.get(kind))
        return bool(
            sample
            and current[1] is not None
            and sample[2] == current
            and kind not in self.errors
            and self.clock() - sample[0] <= self.config.stale_after
        )

    def evidence(self, generation, providers):
        """Only validated getter responses, never a service advertisement alone."""
        with self.lock:
            return next(
                (
                    kind
                    for kind in DETAILS
                    if self._fresh(kind, generation, providers)
                    and any(item["value"] is not None for item in self.values[kind][1])
                ),
                None,
            )

    def snapshot(self, trusted, generation, providers):
        result = {}
        with self.lock:
            for kind, (group, schema) in DETAILS.items():
                sample = self.values.get(kind)
                if sample is None:
                    fields = [
                        self.make_field(key, label, None, unit, READ_SERVICES[kind][0])
                        for key, label, unit in schema
                    ]
                else:
                    fields = deepcopy(sample[1])
                fresh = trusted and self._fresh(kind, generation, providers)
                for item in fields:
                    if item["value"] is not None:
                        item["availability"] = "fresh" if fresh else "stale"
                    elif kind in self.errors and sample is None:
                        item["availability"] = "unavailable"
                    if not fresh:
                        item["reason"] = self.errors.get(kind, "尚无有效样本或来源/服务变化、样本过期")
                result.setdefault(group, []).extend(fields)
        return result
