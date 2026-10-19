"""Strict ROS message conversion using the checked, generated interface catalog."""

import base64
import math

from .catalog import DEFINITIONS


def populate(message, type_name, values, message_types):
    fields = {field["name"]: field["type"] for field in DEFINITIONS[type_name]["fields"]}
    for name, value in values.items():
        field_type = fields[name]
        if field_type.endswith("[]") and field_type[:-2] in DEFINITIONS:
            item_type = field_type[:-2]
            value = [
                populate(getattr(message_types, item_type)(), item_type, item, message_types)
                for item in value
            ]
        elif field_type in DEFINITIONS:
            populate(getattr(message, name), field_type, value, message_types)
            continue
        setattr(message, name, value)
    return message


def stamp_headers(message, stamp, sequence=0):
    """Set only conventional ROS header stamps; never inject a control parameter."""
    if not hasattr(message, "get_fields_and_field_types"):
        return
    for name in message.get_fields_and_field_types():
        value = getattr(message, name)
        if name in ("stamp", "stamps", "meas_stamp") and hasattr(value, "sec"):
            value.sec, value.nanosec = stamp.sec, stamp.nanosec
        elif name == "sequence":
            setattr(message, name, sequence % 2**32)
        elif name in ("header", "request"):
            stamp_headers(value, stamp, sequence)


def plain(value):
    if hasattr(value, "get_fields_and_field_types"):
        return {name: plain(getattr(value, name)) for name in value.get_fields_and_field_types()}
    if isinstance(value, (bytes, bytearray)) or getattr(value, "typecode", None) == "B":
        return {"encoding": "base64", "bytes": len(value), "data": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > 2**53 - 1 else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return [plain(item) for item in value]
