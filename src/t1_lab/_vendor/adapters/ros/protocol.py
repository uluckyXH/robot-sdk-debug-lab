"""ROS schema mapping is independent of rclpy so it can be checked with fake types."""

import base64
import re
from datetime import datetime, timezone

from t1_lab._vendor.contracts.catalog import BridgeError, READ_SERVICES

MAX_JPEG = 8 * 1024 * 1024
MAX_JPEG_EDGE = 8192
MAX_JPEG_PIXELS = 24_000_000


def populate_request(kind, request, arguments, trace_id, stamp):
    if kind in READ_SERVICES:
        request.request.header.stamp = stamp
    elif kind == "frame":
        request.request.header.stamp = stamp
        request.camera_id = arguments["camera_id"]
        request.timeout_ms = 2000
    elif kind in ("set_volume", "set_mute"):
        request.request.header.stamp = stamp
        if kind == "set_volume":
            request.audio_volume = arguments["volume"]
        else:
            request.is_mute = arguments["muted"]
    else:
        request.header.header.stamp = stamp
        if kind == "speak":
            req = request.tts_req
            req.text, req.trace_id, req.domain = arguments["text"], trace_id, "roboweave"
            req.priority_level.value, req.priority_weight = 6, 0
            req.is_interrupted = False
        elif kind == "expression":
            request.type, request.emotion_ids = "emotion", [arguments["emotion_id"]]
            request.file_paths, request.priority, request.loop_count = [], 0, arguments.get("loop_count", 1)
    return request


def common_result(common):
    code = getattr(getattr(common, "header", None), "code", None)
    state = getattr(getattr(common, "status", None), "value", None)
    # Default UNKNOWN can coexist with code=0, but explicit failures must win.
    okay = type(code) is int and code == 0 and type(state) is int and state in (0, 1)
    return okay, code


def diagnostic_text(message):
    if not isinstance(message, str):
        return "", False
    message = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", message)
    message = re.sub(
        r"(?i)((?:token|password|api[_-]?key|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", message
    )
    message = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", message)
    return message[:2048], len(message) > 2048


def request_details(kind, request):
    """Read the actual populated ROS request, excluding transport credentials."""
    if kind == "expression":
        return {
            "type": request.type,
            "emotion_ids": list(request.emotion_ids),
            "file_paths": list(request.file_paths),
            "priority": request.priority,
            "loop_count": request.loop_count,
        }
    if kind == "speak":
        req = request.tts_req
        return {
            "text": req.text,
            "trace_id": req.trace_id,
            "domain": req.domain,
            "priority_level": req.priority_level.value,
            "priority_weight": req.priority_weight,
            "is_interrupted": req.is_interrupted,
        }
    return {name: getattr(request, name) for name in ("audio_volume", "is_mute") if hasattr(request, name)}


def command_result(kind, response):
    common = response.response if kind in ("set_volume", "set_mute") else response.header
    okay, code = common_result(common)
    if kind == "speak":
        okay = okay and response.tts_resp.is_success is True
    result = {
        "status": "accepted" if okay else "failed",
        "execution_confirmed": False,
        "feedback_source": "ros-service-response",
        "detail": "SDK 已受理；执行或播放结束尚未确认" if okay else "SDK 拒绝或返回冲突状态",
    }
    if type(code) is int:
        result["vendor_code"] = code
    state = getattr(getattr(common, "status", None), "value", None)
    if type(state) is int:
        result["vendor_status"] = state
    # Return bounded, plain-text diagnostics to the authenticated operator.
    # Never render vendor content as markup or expose embedded credentials.
    result["vendor_message"], result["vendor_message_truncated"] = diagnostic_text(
        getattr(common, "message", "")
    )
    if kind == "speak" and isinstance(response.tts_resp.trace_id, str):
        result["vendor_trace_id"] = response.tts_resp.trace_id[:128]
        result["tts_success"] = response.tts_resp.is_success is True
        result["tts_error"], result["tts_error_truncated"] = diagnostic_text(
            getattr(response.tts_resp, "error_message", "")
        )
        duration = getattr(response.tts_resp, "estimated_duration", None)
        if type(duration) is int and duration >= 0:
            result["estimated_duration"] = duration
    return result


def jpeg_dimensions(data):
    if len(data) < 4 or len(data) > MAX_JPEG or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise BridgeError(502, "相机未返回有界 JPEG")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 255:
            break
        while offset < len(data) and data[offset] == 255:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9, 0xDA):
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 8:
                break
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            if (
                not 1 <= width <= MAX_JPEG_EDGE
                or not 1 <= height <= MAX_JPEG_EDGE
                or width * height > MAX_JPEG_PIXELS
            ):
                break
            return width, height
        offset += length
    raise BridgeError(502, "JPEG 尺寸头无效或超过资源预算（最长边 8192、2400 万像素）")


def frame_result(response, camera_id):
    if not common_result(response.response)[0]:
        raise BridgeError(502, "相机服务未成功返回")
    jpeg = response.jpeg
    if jpeg.camera_id != camera_id:
        raise BridgeError(502, "相机身份不符")
    if len(jpeg.image.data) > MAX_JPEG:
        raise BridgeError(502, "JPEG 超过单帧 8 MiB 接收预算")
    data = bytes(jpeg.image.data)
    width, height = jpeg_dimensions(data)
    if (
        type(jpeg.width) is not int
        or type(jpeg.height) is not int
        or (width, height) != (jpeg.width, jpeg.height)
    ):
        raise BridgeError(502, "JPEG 与 SDK 声明尺寸不一致")
    stamp = jpeg.image.header.stamp
    timestamp = None
    if (
        type(stamp.sec) is int
        and type(stamp.nanosec) is int
        and stamp.sec > 0
        and 0 <= stamp.nanosec < 1_000_000_000
    ):
        try:
            timestamp = datetime.fromtimestamp(stamp.sec + stamp.nanosec / 1e9, timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            pass
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
        "camera_id": camera_id,
        "width": width,
        "height": height,
        "source_timestamp": timestamp,
        "simulated": False,
    }
