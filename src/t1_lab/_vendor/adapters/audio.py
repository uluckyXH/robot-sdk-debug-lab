"""Explicit local or remote Pulse capture; no SSH, gain or channel guessing."""

import asyncio
import contextlib
import io
import ipaddress
import json
import logging
import os
import re
import stat
from pathlib import Path
import wave

from t1_lab._vendor.contracts.catalog import BridgeError

RATE = 16000
CHUNK_FRAMES = 320
CONTINUOUS_LEASE_SECONDS = 10
CONTINUOUS_READ_TIMEOUT = 3
LOG = logging.getLogger("roboweave.bridge.audio")


def remote_server(value):
    if not isinstance(value, str):
        raise ValueError("audio.server 须为显式 tcp:IPv4:port")
    match = re.fullmatch(r"tcp:([0-9.]+):([0-9]{1,5})", value)
    if not match:
        raise ValueError("audio.server 仅接受单个 tcp:IPv4:port，不接受备用地址或主机名")
    address = ipaddress.IPv4Address(match[1])
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
        raise ValueError("audio.server 需要明确的单播地址")
    if not 1 <= int(match[2]) <= 65535:
        raise ValueError("audio.server 端口无效")
    return value


def audio_parameters(body, defaults):
    if (
        not isinstance(body, dict)
        or not {"seconds"} <= body.keys()
        or body.keys() - {"seconds", "device", "channel"}
    ):
        raise BridgeError(422, "音频参数仅支持 seconds、device、channel")
    seconds = body["seconds"]
    if type(seconds) is not int or not 1 <= seconds <= 20:
        raise BridgeError(422, "seconds 须为 1–20 整数")
    device, channel = body.get("device", defaults.get("device")), body.get("channel", defaults.get("channel"))
    if not isinstance(device, str) or not device or type(channel) is not int:
        raise BridgeError(409, "未登记音频设备与拾音声道")
    if device != defaults.get("device") or channel != defaults.get("channel"):
        raise BridgeError(409, "音频设备或声道不符；禁止猜测/切换未核验来源")
    return seconds, device, channel


def wav_bytes(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(RATE)
        target.writeframes(pcm)
    return buffer.getvalue()


async def reap(process):
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 1)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class PulseAudio:
    def __init__(self, settings):
        self.settings, self.busy = dict(settings), False
        self.transport = settings.get("transport", "unix")
        self.auth = settings.get("auth", "cookie")
        self.server = None
        self.session = None
        if settings:
            expected = {"device", "channel", "channels", "source_rate", "source_format", "verification_note"}
            if self.transport == "tcp":
                expected |= {"transport", "server"}
                if self.auth not in ("cookie", "anonymous"):
                    raise ValueError("audio.auth 须为 cookie 或显式 anonymous")
                if "auth" in settings:
                    expected.add("auth")
                if self.auth == "cookie":
                    expected.add("cookie_file")
            elif self.transport == "unix":
                expected |= {"socket"} | ({"transport"} if "transport" in settings else set())
            else:
                raise ValueError("audio.transport 须为 unix 或 tcp")
            if settings.keys() != expected:
                raise ValueError("audio 配置须登记音源、声道、原始格式、核验记录及当前传输所需的端点/认证")
            if not isinstance(settings["device"], str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,160}", settings["device"]
            ):
                raise ValueError("audio.device 无效")
            if settings["device"].endswith(".monitor"):
                raise ValueError("不把播放回环 monitor 作为机器人麦克风")
            if type(settings["channels"]) is not int or not 1 <= settings["channels"] <= 8:
                raise ValueError("audio.channels 须为 1–8")
            if type(settings["channel"]) is not int or not 0 <= settings["channel"] < settings["channels"]:
                raise ValueError("audio.channel 须为已核验的零基声道索引")
            if settings["source_format"] not in ("s16le", "s24le", "s32le") or settings[
                "source_rate"
            ] not in (
                16000,
                44100,
                48000,
            ):
                raise ValueError("未支持的原始音频格式")
            if self.transport == "unix":
                if not isinstance(settings["socket"], str) or not Path(settings["socket"]).is_absolute():
                    raise ValueError("audio.socket 必须是本机绝对 Unix socket 路径")
                self.server = "unix:" + settings["socket"]
            else:
                self.server = remote_server(settings["server"])
                if self.auth == "cookie" and (
                    not isinstance(settings["cookie_file"], str)
                    or not Path(settings["cookie_file"]).is_absolute()
                ):
                    raise ValueError("远程 Pulse 必须指定本机绝对 cookie_file 路径")
            if (
                not isinstance(settings["verification_note"], str)
                or not settings["verification_note"].strip()
            ):
                raise ValueError("真实拾音声道必须经人工核验")

    @property
    def available(self):
        return bool(self.settings)

    def _connection(self):
        if self.transport == "unix":
            if not stat.S_ISSOCK(Path(self.settings["socket"]).stat().st_mode):
                raise BridgeError(503, "Pulse socket 不可用")
            return {}
        if self.auth == "anonymous":
            # PipeWire's vendor TCP example grants access on the server; it does
            # not authenticate by comparing Pulse cookies. Never infer this
            # mode after a cookie failure or treat it as an authenticated link.
            env = {**os.environ, "PULSE_SERVER": self.server}
            env.pop("PULSE_COOKIE", None)
            return {"env": env}
        # Do not let libpulse silently generate/use a different cookie after an unreadable file.
        cookie = self.settings["cookie_file"]
        try:
            descriptor = os.open(cookie, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & 0o077
                    or metadata.st_size != 256
                    or len(source.read(257)) != 256
                ):
                    raise BridgeError(503, "Pulse cookie 须为仅所有者可读写的 256 字节普通文件")
        except OSError:
            raise BridgeError(503, "远程 Pulse cookie 不可读或不是普通文件；不回退到其他凭据") from None
        return {"env": {**os.environ, "PULSE_SERVER": self.server, "PULSE_COOKIE": cookie}}

    async def _verify_source(self):
        cfg = self.settings
        try:
            connection = self._connection()
            process = await asyncio.create_subprocess_exec(
                "pactl",
                "--server=" + self.server,
                "--format=json",
                "list",
                "sources",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=256 * 1024,
                **connection,
            )
        except OSError:
            raise BridgeError(503, "Pulse 端点或 pactl 不可用") from None
        try:
            # A full pipe/oversize result is rejected and the process is always reaped.
            raw = await asyncio.wait_for(process.stdout.read(256 * 1024 + 1), 2)
            if len(raw) > 256 * 1024:
                raise BridgeError(502, "Pulse 设备列表过大")
            # read() can return a short pipe segment before EOF; collect only to the bound.
            while True:
                chunk = await asyncio.wait_for(process.stdout.read(256 * 1024 + 1 - len(raw)), 2)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > 256 * 1024:
                    raise BridgeError(502, "Pulse 设备列表过大")
            if await asyncio.wait_for(process.wait(), 1) != 0:
                raise BridgeError(503, "无法查询配置的 Pulse 设备；检查端点与认证，不回退到电脑麦克风")
            sources = json.loads(raw)
            matches = [s for s in sources if s.get("name") == cfg["device"]]
            expected = f"{cfg['source_format']} {cfg['channels']}ch {cfg['source_rate']}Hz"
            if len(matches) != 1 or matches[0].get("sample_specification") != expected:
                raise BridgeError(409, "Pulse 设备原始格式或通道数已变化；请重新核验")
            flags = matches[0].get("flags")
            if not isinstance(flags, list) or "NETWORK" in flags or matches[0].get("monitor_source"):
                raise BridgeError(
                    409, "须使用指定 Pulse 服务器的麦克风来源，不能使用转发网络或播放回环 source"
                )
            properties = matches[0].get("properties", {})
            if not isinstance(properties, dict) or properties.get("device.class") == "monitor":
                raise BridgeError(409, "Pulse 来源是播放回环或属性无效")
        except (ValueError, KeyError, AttributeError, TypeError, asyncio.TimeoutError):
            raise BridgeError(503, "Pulse 设备探测失败") from None
        finally:
            await reap(process)

    async def open(self, body):
        if not self.available:
            raise BridgeError(409, "未配置并核验机器人拾音设备")
        seconds, device, channel = audio_parameters(body, self.settings)
        return await self._open(seconds, device, channel)

    async def open_continuous(self, body):
        if (
            not isinstance(body, dict)
            or "capture_id" not in body
            or body.keys() - {"capture_id", "device", "channel"}
            or not isinstance(body["capture_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", body["capture_id"])
        ):
            raise BridgeError(422, "持续采音须提供有效 capture_id；仅接受已登记音源")
        if not self.available:
            raise BridgeError(409, "未配置并核验机器人拾音设备")
        _, device, channel = audio_parameters(
            {"seconds": 1, **{k: v for k, v in body.items() if k != "capture_id"}}, self.settings
        )
        return await self._open(None, device, channel, capture_id=body["capture_id"])

    async def _open(self, seconds, device, channel, *, capture_id=None):
        if self.busy:
            raise BridgeError(409, "当前桥接已有采集；不会排队或自动续录")
        self.busy = True
        process = None
        try:
            await asyncio.wait_for(self._verify_source(), 3)
            channels = self.settings["channels"]
            connection = self._connection()
            process = await asyncio.create_subprocess_exec(
                "parec",
                "--server=" + self.server,
                "--device=" + device,
                "--raw",
                "--format=s16le",
                "--rate=16000",
                "--channels=" + str(channels),
                "--channel-map=" + ",".join("aux" + str(i) for i in range(channels)),
                "--no-remix",
                "--no-remap",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=65536,
                **connection,
            )
            self.session = (
                Capture(self, process, seconds, channels, channel)
                if capture_id is None
                else ContinuousCapture(self, process, channels, channel, capture_id)
            )
            LOG.info(
                "audio capture transport=%s device=%s channel=%s seconds=%s",
                self.transport,
                device,
                channel,
                seconds,
            )
            return self.session
        except BaseException as exc:
            if process is not None:
                await reap(process)
            self.busy = False
            if isinstance(exc, (OSError, asyncio.TimeoutError)):
                raise BridgeError(503, "音频采集程序不可用或端点探测超时") from None
            raise

    async def close(self):
        if self.session is not None:
            await self.session.close()


class Capture:
    def __init__(self, owner, process, seconds, channels, channel):
        self.owner, self.process = owner, process
        self.seconds, self.channels, self.channel = seconds, channels, channel
        self.closed = False
        self.close_task = None
        self.watchdog = asyncio.create_task(self._expire())

    async def _expire(self):
        await asyncio.sleep(self.seconds + 3)
        await self.close()

    async def chunks(self):
        deadline = asyncio.get_running_loop().time() + self.seconds + 3
        try:
            for _ in range(self.seconds * RATE // CHUNK_FRAMES):
                raw = await asyncio.wait_for(
                    self.process.stdout.readexactly(CHUNK_FRAMES * self.channels * 2),
                    max(0.01, deadline - asyncio.get_running_loop().time()),
                )
                # Retain byte order; no host-endian array conversion or mixed channels.
                stride, start = self.channels * 2, self.channel * 2
                yield b"".join(raw[i : i + 2] for i in range(start, len(raw), stride))
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            raise BridgeError(502, "音频采集提前结束或超时；不会补零或重试") from None
        finally:
            await self.close()

    async def close(self):
        if self.close_task is None:
            self.closed = True
            if asyncio.current_task() is not self.watchdog:
                self.watchdog.cancel()

            async def cleanup():
                try:
                    await reap(self.process)
                finally:
                    self.owner.busy = False
                    self.owner.session = None

            self.close_task = asyncio.create_task(cleanup())
        await asyncio.shield(self.close_task)


class ContinuousCapture(Capture):
    """One parec process, bounded reads and an independently expiring application lease."""

    def __init__(self, owner, process, channels, channel, capture_id):
        self.capture_id = capture_id
        self.lease_seconds = CONTINUOUS_LEASE_SECONDS
        self.expires_at = asyncio.get_running_loop().time() + self.lease_seconds
        self.expired = False
        super().__init__(owner, process, None, channels, channel)

    def renew(self):
        clock = asyncio.get_running_loop().time()
        if self.closed or clock >= self.expires_at:
            raise BridgeError(409, "持续采音租约已失效，不能恢复旧采集")
        self.expires_at = clock + self.lease_seconds
        return {"capture_id": self.capture_id, "lease_seconds": self.lease_seconds}

    async def _expire(self):
        while not self.closed:
            remaining = self.expires_at - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.expired = True
                await self.close()
                return
            await asyncio.sleep(remaining)

    async def chunks(self):
        try:
            while not self.closed:
                raw = await asyncio.wait_for(
                    self.process.stdout.readexactly(CHUNK_FRAMES * self.channels * 2), CONTINUOUS_READ_TIMEOUT
                )
                if self.closed:
                    break
                stride, start = self.channels * 2, self.channel * 2
                yield b"".join(raw[i : i + 2] for i in range(start, len(raw), stride))
            if self.expired:
                raise BridgeError(502, "持续采音租约失效")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            if self.closed and not self.expired:
                return  # An explicit close ends HTTP; an unexpected EOF still fails below.
            raise BridgeError(502, "持续采音断流或超时；不会补静音或重连") from None
        finally:
            await self.close()
