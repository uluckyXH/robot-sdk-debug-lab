"""Interface facts verified against the two pinned vendor SDK trees."""

from datetime import datetime, timezone

from t1_lab._vendor.families import CAMERAS, COMMITS, EMOTIONS

TOPICS = {
    "battery": ("/aima/hal/bms/state", "Bms"),
    "joints": ("/aima/hal/joint/state", "JointStateArray"),
    "interaction": ("/aima/hal/touch/state", "TouchState"),
}
SERVICES = {
    "frame": ("/aima/hal/camera/CaptureJpegImage", "CaptureJpegImage"),
    "speak": ("/aimdk_5Fmsgs/srv/PlayTts", "PlayTts"),
    "expression": ("/aimdk_5Fmsgs/srv/PlayEmotion", "PlayEmotion"),
    "set_volume": ("/aimdk_5Fmsgs/srv/SetVolume", "SetVolume"),
    "set_mute": ("/aimdk_5Fmsgs/srv/SetMute", "SetMute"),
}
COMMANDS = frozenset(("speak", "expression", "set_volume", "set_mute"))
READ_SERVICES = {
    "get_mc_action": ("/aimdk_5Fmsgs/srv/GetMcAction", "GetMcAction"),
    "get_input_source": ("/aimdk_5Fmsgs/srv/GetCurrentInputSource", "GetCurrentInputSource"),
    "get_volume": ("/aimdk_5Fmsgs/srv/GetVolume", "GetVolume"),
    "get_mute": ("/aimdk_5Fmsgs/srv/GetMute", "GetMute"),
}
SERVICES.update(READ_SERVICES)
BMS_FIELDS = (
    ("remaining_capacity_percentage", "电量", "%"),
    ("voltage", "电池电压", "mV"),
    ("current", "电池电流", "mA"),
    ("power", "电池功率", "mW"),
    ("temperature", "电池温度", "°C"),
    ("remaining_capacity", "剩余容量", "mAh"),
    ("cycle_count", "循环次数", "cycles"),
    ("status", "电池状态位", None),
)
JOINT_FIELDS = (
    ("position", "位置", "rad"),
    ("velocity", "速度", "rad/s"),
    ("effort", "力矩", "Nm"),
    ("coil_temp", "线圈温度", "°C"),
    ("motor_temp", "电机温度", "°C"),
    ("motor_vol", "电机电压", "V"),
)


def now():
    return datetime.now(timezone.utc).isoformat()


class BridgeError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


def fields_only(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise BridgeError(422, "字段必须为：" + ", ".join(sorted(required)))


def command_arguments(family, kind, args):
    if not isinstance(kind, str) or kind not in COMMANDS:
        raise BridgeError(422, "未开放此命令")
    if kind == "speak":
        fields_only(args, {"text"})
        if not isinstance(args["text"], str) or not args["text"].strip() or len(args["text"]) > 2000:
            raise BridgeError(422, "text 须为非空文本，最多 2000 字符")
    elif kind == "expression":
        if set(args) not in ({"emotion_id"}, {"emotion_id", "loop_count"}):
            raise BridgeError(422, "表情字段须为 emotion_id 与可选 loop_count")
        if type(args["emotion_id"]) is not int or args["emotion_id"] not in EMOTIONS[family]:
            raise BridgeError(422, "emotion_id 不在当前型号白名单")
        if "loop_count" in args and (
            type(args["loop_count"]) is not int or not 1 <= args["loop_count"] <= 10
        ):
            raise BridgeError(422, "loop_count 须为 1–10 次")
    elif kind == "set_volume":
        fields_only(args, {"volume"})
        if type(args["volume"]) is not int or not 0 <= args["volume"] <= 100:
            raise BridgeError(422, "volume 须为 0–100 整数")
    else:
        fields_only(args, {"muted"})
        if type(args["muted"]) is not bool:
            raise BridgeError(422, "muted 须为布尔值")
    return args
