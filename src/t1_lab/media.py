"""Shared, bounded RTSP decoding; each viewer owns a subscription, not a process."""

import asyncio
import contextlib
import json
import time
import uuid
from pathlib import Path

from t1_lab._vendor.adapters.audio import reap
from t1_lab._vendor.adapters.ros.protocol import jpeg_dimensions
from t1_lab._vendor.contracts.catalog import BridgeError, now


class VideoStream:
    def __init__(self, settings, camera):
        self.settings, self.camera = settings, camera
        self.subscribers = set()
        self.task = None
        self.process = None
        self.latest = None
        self.updated = 0.0
        self.received_at = None
        self.frames = 0
        self.error = None
        self.lock = asyncio.Lock()

    def status(self):
        live = self.latest is not None and time.monotonic() - self.updated < 3
        return {
            "status": "live" if live else "error" if self.error else "connecting" if self.task else "idle",
            "camera": self.camera,
            "frames": self.frames,
            "received_at": self.received_at,
            "subscribers": len(self.subscribers),
            "error": self.error,
            "fps": self.settings.video_fps,
        }

    @contextlib.asynccontextmanager
    async def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        async with self.lock:
            self.subscribers.add(queue)
            if self.task is None or self.task.done():
                self.error = None
                self.task = asyncio.create_task(self._decode())
            if self.latest is not None and time.monotonic() - self.updated < 2:
                queue.put_nowait(self.latest)
        try:
            yield queue
        finally:
            async with self.lock:
                self.subscribers.discard(queue)
                if not self.subscribers:
                    await self.close()

    def _broadcast(self, value):
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(value)

    async def _decode(self):
        try:
            # FFmpeg versions differ in the RTSP socket timeout option name.
            probe = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-h",
                "demuxer=rtsp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                help_text, _ = await asyncio.wait_for(probe.communicate(), 3)
            finally:
                await reap(probe)
            timeout = "-stimeout" if b"-stimeout" in help_text else "-timeout"
            self.process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-rtsp_transport",
                "tcp",
                timeout,
                "7000000",
                "-i",
                self.settings.rtsp_url(self.camera),
                "-an",
                "-threads",
                "2",
                "-vf",
                f"fps={self.settings.video_fps},scale={self.settings.video_width}:-2",
                "-q:v",
                "5",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=256 * 1024,
            )
            buffer = bytearray()
            while True:
                chunk = await asyncio.wait_for(self.process.stdout.read(65536), 12)
                if not chunk:
                    raise BridgeError(502, "RTSP 视频流已结束，请检查机器人相机或重新连接")
                buffer.extend(chunk)
                if len(buffer) > 8 * 1024 * 1024:
                    raise BridgeError(502, "视频帧超过 8 MiB 限制")
                while True:
                    start = buffer.find(b"\xff\xd8")
                    end = buffer.find(b"\xff\xd9", max(0, start))
                    if start < 0 or end < 0:
                        break
                    frame = bytes(buffer[start : end + 2])
                    del buffer[: end + 2]
                    jpeg_dimensions(frame)
                    self.latest, self.updated, self.received_at = frame, time.monotonic(), now()
                    self.frames += 1
                    self.error = None
                    self._broadcast(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = (
                exc.detail
                if isinstance(exc, BridgeError)
                else "视频连接超时，请检查 RTSP 地址和网络"
                if isinstance(exc, asyncio.TimeoutError)
                else "无法启动视频解码，请确认 FFmpeg 已安装及相机可达"
            )
            self._broadcast(BridgeError(502, self.error))
        finally:
            if self.process is not None:
                await reap(self.process)
                self.process = None
            self.latest = None

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.latest = None


class VideoHub:
    def __init__(self, settings):
        self.streams = {camera: VideoStream(settings, camera) for camera in ("left", "right")}

    async def frame(self, camera):
        async with self.streams[camera].subscribe() as queue:
            return await next_frame(queue)

    async def close(self):
        await asyncio.gather(*(stream.close() for stream in self.streams.values()))


async def next_frame(queue):
    try:
        value = await asyncio.wait_for(queue.get(), 15)
    except asyncio.TimeoutError:
        raise BridgeError(504, "等待视频帧超时") from None
    if isinstance(value, Exception):
        raise value
    return value


class Snapshots:
    def __init__(self, data_dir, limit=60):
        self.root = Path(data_dir) / "snapshots"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limit = limit

    def list(self):
        items = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                if (self.root / (path.stem + ".jpg")).is_file():
                    items.append(data)
            except (ValueError, OSError):
                continue
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def save(self, data, camera, source):
        width, height = jpeg_dimensions(data)
        identifier = uuid.uuid4().hex
        item = {
            "id": identifier,
            "camera": camera,
            "source": source,
            "created_at": now(),
            "width": width,
            "height": height,
            "bytes": len(data),
            "url": f"/api/snapshots/{identifier}.jpg",
        }
        (self.root / (identifier + ".jpg")).write_bytes(data)
        (self.root / (identifier + ".json")).write_text(json.dumps(item))
        for expired in self.list()[self.limit :]:
            for extension in ("jpg", "json"):
                (self.root / (expired["id"] + "." + extension)).unlink(missing_ok=True)
        return item
