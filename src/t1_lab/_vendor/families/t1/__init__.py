"""T1 facts from the pinned SDK; no ROS imports or device calls."""

SDK_COMMIT = "fe2e186c867391ec78b301accdc91f0260ad0ff3"
CAMERAS = ("head_stereo_left", "head_stereo_right")
EMOTIONS = frozenset((*range(10, 24), 60, 90, 102, 130, 230, 240, 250, 260, 270))
PRESET_MODE = 300
PRESETS = {
    "t1.right_wave": {"id": 1001, "enum": "RIGHT_ARM_WAVE", "label": "右手挥手"},
    "t1.left_wave": {"id": 1011, "enum": "LEFT_ARM_WAVE", "label": "左手挥手"},
    "t1.default_pose": {"id": 0, "enum": "DEFAULT_POS", "label": "恢复上肢默认姿态"},
    "t1.right_handshake": {"id": 1002, "enum": "RIGHT_ARM_HANDSHAKE", "label": "右手握手"},
    "t1.right_handshake_high": {
        "id": 1003,
        "enum": "RIGHT_ARM_HANDSHAKE_FOR_1D5METER",
        "label": "右手高位握手",
    },
    "t1.left_handshake": {"id": 1012, "enum": "LEFT_ARM_HANDSHAKE", "label": "左手握手"},
    "t1.left_handshake_high": {
        "id": 1013,
        "enum": "LEFT_ARM_HANDSHAKE_FOR_1D5METER",
        "label": "左手高位握手",
    },
    "t1.make_heart": {"id": 2001, "enum": "BOTH_HANDS_MAKE_HEART", "label": "双手比心"},
    "t1.happy_gesture": {"id": 2002, "enum": "BOTH_HANDS_ACTION_HAPPY", "label": "开心动作"},
    "t1.talking_gesture": {"id": 2003, "enum": "BOTH_HANDS_ACTION_TALKING", "label": "说话动作"},
    **{
        f"t1.talk_{i}": {"id": 2003 + i, "enum": f"T1_TALK{i}", "label": f"说话姿态 {i}"} for i in range(1, 7)
    },
    **{
        f"t1.photo_{i}": {"id": 2010 + i, "enum": f"PHOTO_POSE_{i}", "label": f"拍照姿态 {i}"}
        for i in range(1, 5)
    },
    "t1.photo_three": {"id": 2020, "enum": "PHOTO_THREE_SHOT", "label": "三连拍姿态"},
}
JOINT_TEMPERATURE_TYPE = "uint8"
TOUCH_LABELS = {1: "背部", 2: "胸部"}
