"""T1 semantic actions and normal posture transitions from SDK fe2e186."""

from collections import deque

MODE_NAMES = {
    1: "PASSIVE_DEFAULT",
    2: "DAMPING_DEFAULT",
    3: "QUAD_LIE_DOWN",
    10: "QUADRUPED_TO_BIPED",
    20: "BIPED_TO_QUADRUPED",
    101: "QUADRUPED_STAND_DEFAULT",
    102: "QUADRUPED_LOCOMOTION_DEFAULT",
    103: "QUADRUPED_LOCOMOTION_TERRAIN",
    104: "QUADRUPED_LOCOMOTION_BIONIC",
    107: "QUADRUPED_LOCOMOTION_HANDSHAKE",
    109: "QUADRUPED_LOCOMOTION_DANCE",
    110: "QUADRUPED_GET_DOWN_DEFAULT",
    111: "QUADRUPED_SIT_DOWN_DEFAULT",
    112: "QUADRUPED_LOCOMOTION_RUN",
    113: "QUADRUPED_LOCOMOTION_PRIDE",
    114: "QUADRUPED_LOCOMOTION_PLEASURE",
    116: "QUADRUPED_LOCOMOTION_JUMP",
    117: "QUADRUPED_LOCOMOTION_STRETCH",
    118: "QUADRUPED_PUSH_UP",
    121: "QUADRUPED_LOCOMOTION_STEP",
    201: "BIPED_STAND_DEFAULT",
    202: "BIPED_LOCOMOTION_DEFAULT",
    203: "BIPED_LOCOMOTION_TERRAIN",
    204: "BIPED_LOCOMOTION_ROTATE_LOCAL",
    206: "BIPED_LOCOMOTION_MOONWALK",
    207: "BIPED_LOCOMOTION_RUN",
    300: "BIPED_LOCOMOTION_WBC",
    301: "BIPED_LOCOMOTION_ANIMATION",
    302: "BIPED_RECORD_UPPER",
    1014: "BIPED_CUSTOM_UPPER",
}
# Intermediate steps are normal conversions, never tricks or a passive fall.
EDGES = {
    1: (101, 110, 111),
    101: (102, 110, 111),
    110: (101, 111),
    111: (101, 110),
    102: (101, 110, 111, 10),
    300: (20, 202, 203, 207, 301, 302, 1014),
    202: (300, 203, 207),
    203: (300,),
    207: (300,),
    301: (300, 20),
    302: (300,),
    1014: (300,),
    103: (102, 101, 110, 111, 10),
    104: (102,),
    107: (102, 101),
    109: (102, 101),
    112: (102, 101, 110, 111, 10),
    113: (102,),
    114: (102,),
    116: (102, 101),
    117: (102, 101),
    118: (102, 101),
    121: (102,),
    204: (300,),
    206: (300,),
}
ARRIVAL = {10: 300, 20: 102, 201: 300}


def transition_path(current, target):
    """Return (requested mode, observed destination) steps, without shortcuts."""
    current, target = ARRIVAL.get(current, current), ARRIVAL.get(target, target)
    queue = deque([(current, [])])
    visited = {current}
    while queue:
        source, path = queue.popleft()
        if source == target:
            return path
        for command in EDGES.get(source, ()):
            destination = ARRIVAL.get(command, command)
            if destination not in visited:
                visited.add(destination)
                queue.append((destination, [*path, (command, destination)]))
    raise ValueError(
        f"当前模式 {MODE_NAMES.get(current, current)} 没有到 {MODE_NAMES.get(target, target)} 的常规转换路径"
    )


def actions(presets):
    result = {
        "stand_quadruped": {"label": "四足站立", "kind": "mode", "mode": 101},
        "stand_biped": {"label": "双足站立", "kind": "mode", "mode": 300},
        "lie_down": {"label": "趴下", "kind": "mode", "mode": 110},
        "sit_down": {"label": "坐下", "kind": "mode", "mode": 111},
    }
    for name, label, axis, sign in (
        ("walk_forward_short", "前进", 0, 1),
        ("walk_backward_short", "后退", 0, -1),
        ("move_left_short", "左移", 1, 1),
        ("move_right_short", "右移", 1, -1),
        ("turn_left_short", "左转", 2, 1),
        ("turn_right_short", "右转", 2, -1),
    ):
        result[name] = {"label": label, "kind": "velocity", "axis": axis, "sign": sign}
    for preset, value in presets.items():
        name = {"t1.left_wave": "wave_left", "t1.right_wave": "wave_right"}.get(preset, preset.split(".")[1])
        result[name] = {"label": value["label"], "kind": "preset", "preset": preset, "mode": 300}
    return result
