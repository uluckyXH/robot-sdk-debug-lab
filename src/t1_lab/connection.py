"""Local connection configuration only; no ROS imports, sockets, or device calls."""

from copy import deepcopy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

PLACEHOLDER = "{robot_ip}"
DDS_NAMESPACE = "http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles"


def validate_connection(address, domain):
    if not isinstance(address, str):
        raise ValueError("机器人地址须为 IPv4 地址")
    try:
        parsed = ipaddress.IPv4Address(address.strip())
    except ipaddress.AddressValueError as exc:
        raise ValueError("请输入完整的机器人 IPv4 地址，例如 192.168.4.1") from exc
    if parsed.is_multicast or parsed.is_unspecified or str(parsed) == "255.255.255.255":
        raise ValueError("机器人地址须为单播 IPv4 地址")
    if type(domain) is not int or not 0 <= domain <= 232:
        raise ValueError("ROS domain 须为 0–232 的整数")
    return str(parsed), domain


def config_id(path):
    return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:20]


def read_config(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("连接配置须为 JSON 对象")
    return value, hashlib.sha256(raw).hexdigest()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bind_media_to_robot(value):
    """Migrate media addresses on this robot to templates; keep separate servers."""
    value = deepcopy(value)
    address = value["robot_ip"]
    audio = value.get("audio", {})
    if audio.get("transport") == "tcp":
        prefix = f"tcp:{address}:"
        if audio.get("server", "").startswith(prefix):
            audio["server"] = "tcp:" + PLACEHOLDER + ":" + audio["server"][len(prefix) :]
    for camera, url in value.get("rtsp_urls", {}).items():
        parsed = urlsplit(url)
        if parsed.hostname == address:
            # Replace the authority host only; preserve credentials, port and path.
            user, separator, host = parsed.netloc.rpartition("@")
            authority = (user + separator if separator else "") + PLACEHOLDER + host[len(address) :]
            value["rtsp_urls"][camera] = urlunsplit(parsed._replace(netloc=authority))
    return value


def resolve_media(value):
    value = deepcopy(value)
    address = value["robot_ip"]
    audio = value.get("audio", {})
    if isinstance(audio.get("server"), str):
        audio["server"] = audio["server"].replace(PLACEHOLDER, address)
    value["rtsp_urls"] = {
        key: url.replace(PLACEHOLDER, address) for key, url in value.get("rtsp_urls", {}).items()
    }
    return value


def update_connection(path, *, robot_ip=None, ros_domain_id=None, expected_revision=None):
    """Atomically save only connection fields, retaining all control preferences."""
    path = Path(path).resolve()
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        current, revision = read_config(path)
        if expected_revision is not None and revision != expected_revision:
            raise ValueError("连接配置已被其他页面或命令修改，请刷新后再保存")
        address, domain = validate_connection(
            robot_ip if robot_ip is not None else current.get("robot_ip"),
            ros_domain_id if ros_domain_id is not None else current.get("ros_domain_id", 0),
        )
        updated = bind_media_to_robot(current)
        updated.update(robot_ip=address, ros_domain_id=domain)
        if updated != current:
            atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
        return read_config(path)


def dds_template():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<profiles xmlns="{DDS_NAMESPACE}">
  <transport_descriptors><transport_descriptor>
    <transport_id>t1_udp</transport_id><type>UDPv4</type>
    <sendBufferSize>20971520</sendBufferSize><receiveBufferSize>20971520</receiveBufferSize>
    <maxInitialPeersRange>64</maxInitialPeersRange>
  </transport_descriptor></transport_descriptors>
  <participant profile_name="t1_lab" is_default_profile="true"><rtps>
    <useBuiltinTransports>false</useBuiltinTransports>
    <userTransports><transport_id>t1_udp</transport_id></userTransports>
    <builtin><initialPeersList>
      <locator><udpv4><address>239.255.0.1</address><port>0</port></udpv4></locator>
      <locator><udpv4><address>{PLACEHOLDER}</address><port>0</port></udpv4></locator>
    </initialPeersList></builtin>
  </rtps></participant>
</profiles>
'''


def materialize_dds(config_path, config, runtime_root):
    """Render the template before launching a fresh ROS process."""
    address, _ = validate_connection(config.get("robot_ip"), config.get("ros_domain_id", 0))
    root = Path(config_path).resolve().parent.parent
    source = root / config.get("dds_profile", "config/fastdds.xml")
    original = source.read_text() if source.is_file() else dds_template()
    tree = ET.fromstring(original.replace(PLACEHOLDER, address))
    # Compatibility with the old generated profile, which embedded one literal IP.
    if PLACEHOLDER not in original:
        namespace = {"d": DDS_NAMESPACE}
        for participant in tree.findall("d:participant[@profile_name='t1_lab']", namespace):
            nodes = participant.findall(
                "d:rtps/d:builtin/d:initialPeersList/d:locator/d:udpv4/d:address", namespace
            )
            unicast = [
                node for node in nodes if node.text and not ipaddress.IPv4Address(node.text).is_multicast
            ]
            if len(unicast) == 1:
                unicast[0].text = address
    ET.register_namespace("", DDS_NAMESPACE)
    destination = Path(runtime_root) / "connections" / (config_id(config_path) + ".xml")
    atomic_write(destination, ET.tostring(tree, encoding="unicode", xml_declaration=True) + "\n")
    return destination


def connection_view(path, settings, *, automatic_restart=False):
    saved, revision = read_config(path)
    resolved = resolve_media(saved)
    address, domain = validate_connection(saved.get("robot_ip"), saved.get("ros_domain_id", 0))
    independent = []
    audio = resolved.get("audio", {})
    if audio.get("transport") == "tcp" and not audio.get("server", "").startswith(f"tcp:{address}:"):
        independent.append("麦克风")
    for camera, url in resolved.get("rtsp_urls", {}).items():
        if urlsplit(url).hostname != address:
            independent.append("左目视频" if camera == "left" else "右目视频")
    return {
        "robot_ip": address,
        "ros_domain_id": domain,
        "active_robot_ip": settings.robot_ip,
        "active_ros_domain_id": settings.ros_domain_id,
        "pending_restart": address != settings.robot_ip or domain != settings.ros_domain_id,
        "automatic_restart": automatic_restart,
        "revision": revision,
        "independent_media": independent,
    }
