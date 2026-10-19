"""Bounded snapshots with graph evidence, receipt timestamps and explicit staleness."""

from copy import deepcopy
import math
import threading
import time

from t1_lab._vendor.contracts.catalog import BMS_FIELDS, JOINT_FIELDS, TOPICS, now
from t1_lab._vendor.adapters.ros.details import battery_details
from t1_lab._vendor.families import FAMILIES


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def field(key, label, value, unit=None, source="bridge", *, verified=False):
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "source": source,
        "observed_at": None,
        "availability": "unknown",
        "scale_verified": verified,
    }


class Telemetry:
    def __init__(self, config, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.lock = threading.RLock()
        self.graph_at = None
        self.graph = {}
        self.generations = {k: 0 for k in TOPICS}
        self.stable = {k: 0 for k in TOPICS}
        self.samples = {}

    def observe_graph(self, graph):
        """graph values are sorted tuples of (GID hex, fully qualified ROS type), or None."""
        with self.lock:
            self.graph_at = self.clock()
            for key in TOPICS:
                endpoints = graph.get(key)
                if endpoints != self.graph.get(key) or endpoints is None:
                    self.generations[key] += 1
                    self.stable[key] = 0
                self.graph[key] = endpoints
                self.stable[key] += 1

    def _unique(self, key):
        endpoints = self.graph.get(key)
        return (
            endpoints is not None
            and len(endpoints) == 1
            and bool(endpoints[0][0])
            and endpoints[0][1] == "aimdk_msgs/msg/" + TOPICS[key][1]
        )

    def _fresh(self, key):
        sample = self.samples.get(key)
        return bool(
            sample
            and self.graph_at is not None
            and self.clock() - self.graph_at <= 2
            and self._unique(key)
            and self.stable[key] >= 2
            and sample[0] == self.generations[key]
            and self.clock() - sample[1] <= self.config.stale_after
        )

    def conflict(self):
        """Known conflicting publishers cannot be hidden by a healthy getter."""
        with self.lock:
            if self.graph_at is None or self.clock() - self.graph_at > 2:
                return None
            return next(
                (key for key, endpoints in self.graph.items() if endpoints and not self._unique(key)), None
            )

    def evidence(self, key=None):
        with self.lock:
            conflict = self.conflict()
            candidates = (key,) if key is not None else TOPICS
            selected = next(
                (
                    k
                    for k in candidates
                    if self._fresh(k) and any(item["value"] is not None for item in self.samples[k][2])
                ),
                None,
            )
            verified = selected is not None and conflict is None
            selected = conflict or selected or key or "battery"
            endpoints = self.graph.get(selected)
            graph_current = self.graph_at is not None and self.clock() - self.graph_at <= 2
            return {
                "source_count": len(endpoints) if graph_current and endpoints is not None else None,
                "source_verified": verified,
                "source_key": selected,
                "source_method": "graph_singleton_and_fresh_sample",
                "source_reason": "DDS 图唯一来源及新样本；不证明整机序列号"
                if verified
                else "来源未知、多源或样本过期",
            }

    def ingest(self, key, message):
        source = TOPICS[key][0]
        verified = self.config.scale_verified
        if key == "battery":
            fields = [
                field(name, label, number(getattr(message, name, None)), unit, source, verified=verified)
                for name, label, unit in BMS_FIELDS
            ]
            fields.extend(battery_details(message, self.config.family, field))
        elif key == "joints":
            joints = message.joints
            if len(joints) > 32:
                fields = [field("joint_state", "关节状态", None, source=source)]
                fields[0]["reason"] = "关节数量超过 32，不发布截断快照"
            else:
                fields = []
                names = set()
                for joint in joints:
                    name = getattr(joint, "name", "")
                    if not isinstance(name, str) or not name or len(name) > 64 or name in names:
                        fields = [field("joint_state", "关节状态", None, source=source)]
                        fields[0]["reason"] = "关节名无效或重复"
                        break
                    names.add(name)
                    fields.extend(
                        field(
                            name + "." + attr,
                            name + " " + label,
                            number(getattr(joint, attr, None)),
                            unit,
                            source,
                            verified=verified,
                        )
                        for attr, label, unit in JOINT_FIELDS
                    )
        else:
            sensor = getattr(message, "sensor_id", None)
            label = FAMILIES[self.config.family].TOUCH_LABELS.get(sensor)
            fields = [
                field("touch_sensor", "最近触摸部位", label, source=source),
                field(
                    "touch_event",
                    "最近触摸事件编号",
                    number(getattr(message, "event_type", None)),
                    source=source,
                ),
            ]
        with self.lock:
            stamp = now()
            for item in fields:
                item["observed_at"] = stamp
            self.samples[key] = (self.generations[key], self.clock(), fields)

    def generation(self):
        with self.lock:
            return self.generations["battery"]

    def missing_reason(self, key):
        topic = TOPICS[key][0]
        endpoints = self.graph.get(key)
        if self.graph_at is None or self.clock() - self.graph_at > 2 or endpoints is None:
            return f"{topic}：DDS 发布者信息尚未取得或已过期"
        if not endpoints:
            extra = (
                "；T1 厂家关节示例要求低层开发模式，普通运控模式下不保证发布；不要为读取状态自动切换模式"
                if key == "joints" and self.config.family == "t1"
                else ""
            )
            return f"{topic}：当前未发现发布者{extra}"
        if not self._unique(key):
            return f"{topic}：发布者不唯一、标识缺失或类型与 SDK 不匹配"
        if key == "interaction":
            return f"{topic}：已发现发布者，尚未收到触摸事件；无事件不表示机器人离线"
        return f"{topic}：已发现发布者，尚未收到有效样本"

    def snapshot(self):
        with self.lock:
            result = {}
            for key in TOPICS:
                sample = self.samples.get(key)
                if sample is None:
                    result[key] = [field(key, "尚未收到样本", None, source=TOPICS[key][0])]
                    result[key][0]["reason"] = self.missing_reason(key)
                    continue
                fields = deepcopy(sample[2])
                for item in fields:
                    item["availability"] = (
                        "unknown" if item["value"] is None else "fresh" if self._fresh(key) else "stale"
                    )
                    if item["availability"] == "stale":
                        item["reason"] = "来源变化/不唯一、DDS 图过期或样本过期"
                result[key] = fields
            result["system"] = [field("board_memory", "板载内存", None, source="SDK 未提供")]
            return result
