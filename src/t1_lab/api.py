import asyncio
import contextlib
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from t1_lab._vendor.contracts.catalog import BridgeError, now
from t1_lab.media import Snapshots, VideoHub, next_frame
from t1_lab.sdk import T1Client, load_settings
from t1_lab.connection import config_id, connection_view, update_connection


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SnapshotBody(Body):
    camera: Literal["left", "right"] = "left"
    source: Literal["sdk", "video"] = "video"


class ConnectionBody(Body):
    robot_ip: str = Field(min_length=7, max_length=15)
    ros_domain_id: int = Field(ge=0, le=232)
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]+$")


class CommandBody(Body):
    command_id: str = Field(min_length=34, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    kind: Literal["speak", "expression", "set_volume", "set_mute"]
    arguments: dict


class MotionId(Body):
    command_id: str = Field(min_length=34, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class MotionBody(MotionId):
    action: str = Field(max_length=64)


class InterfaceBody(MotionId):
    interface: str = Field(min_length=1, max_length=64)
    payload: dict


class PublishBody(InterfaceBody):
    rate_hz: int = Field(default=1, ge=1, le=1000)
    duration_ms: int = Field(default=1000, ge=1, le=10000)


def same_origin(headers):
    origin = headers.get("origin")
    if not origin:
        return True  # Local non-browser SDK clients.
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and parsed.netloc == headers.get("host")


class VideoResponse(StreamingResponse):
    def __init__(self, content, subscription):
        self.subscription = subscription
        super().__init__(content, media_type="multipart/x-mixed-replace; boundary=frame")

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(self.subscription.__aexit__(None, None, None))


async def first_frame(request, queue):
    async def disconnected():
        while (await request.receive())["type"] != "http.disconnect":
            pass

    frame = asyncio.create_task(next_frame(queue))
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((frame, watcher), return_when=asyncio.FIRST_COMPLETED)
        if frame in done:
            return frame.result()
        raise BridgeError(499, "视频客户端已断开")
    finally:
        for task in (frame, watcher):
            task.cancel()
        await asyncio.gather(frame, watcher, return_exceptions=True)


def create_app(settings=None, *, client=None, video=None, config_path=None):
    settings = settings or load_settings()
    client = client or T1Client(settings)
    video = video or VideoHub(settings)
    snapshots = Snapshots(settings.data_dir)
    events = deque(maxlen=100)
    snapshot_lock = asyncio.Lock()
    config_path = Path(config_path).resolve() if config_path is not None else None
    automatic_restart = os.environ.get("T1_LAB_CONFIG_WATCHER") == str(config_path)

    def event(operation, status, detail, elapsed_ms=None):
        events.appendleft(
            {
                "id": uuid.uuid4().hex,
                "time": now(),
                "operation": operation,
                "status": status,
                "detail": detail,
                "elapsed_ms": elapsed_ms,
            }
        )

    @asynccontextmanager
    async def lifespan(app):
        await client.start()
        event(
            "SDK.connect",
            "success" if client.adapter else "error",
            "T1 SDK 已加载，等待机器人数据" if client.adapter else client.error,
        )
        try:
            yield
        finally:
            await video.close()
            await client.close()

    app = FastAPI(title="T1 SDK Lab", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
    app.state.client, app.state.video = client, video

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request.headers):
            return JSONResponse({"detail": "仅接受同源页面请求"}, status_code=403)
        if request.method == "POST" and (
            request.url.path == "/api/commands" or request.url.path.startswith("/api/motion")
        ):
            try:
                client.require_write()
            except BridgeError as exc:
                event(request.url.path, "blocked", exc.detail)
                return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BridgeError)
    async def bridge_error(request, exc):
        event(request.url.path, "error", exc.detail)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "read_only": settings.read_only,
            "service": "t1-sdk-lab",
            "robot_ip": settings.robot_ip,
            "ros_domain_id": settings.ros_domain_id,
            "config_id": config_id(config_path) if config_path else None,
            "automatic_restart": automatic_restart,
        }

    def configured_connection():
        if config_path is None:
            raise BridgeError(409, "当前嵌入式服务没有可修改的配置文件；请使用 start.sh 启动")
        try:
            return connection_view(config_path, settings, automatic_restart=automatic_restart)
        except (ValueError, OSError) as exc:
            raise BridgeError(422, str(exc)) from exc

    @app.get("/api/connection")
    async def get_connection():
        return configured_connection()

    @app.post("/api/connection")
    async def save_connection(body: ConnectionBody):
        configured_connection()
        if (
            (client.motion_runtime and client.motion_runtime.active)
            or client.interfaces.stream is not None
            or client.interfaces.operation_lock.locked()
        ):
            raise BridgeError(409, "请先结束当前动作或 SDK 发布，待回执返回后再切换连接")
        try:
            update_connection(
                config_path,
                robot_ip=body.robot_ip,
                ros_domain_id=body.ros_domain_id,
                expected_revision=body.revision,
            )
        except (ValueError, OSError) as exc:
            raise BridgeError(422, str(exc)) from exc
        event("connection.save", "success", "连接配置已保存，下次启动按新地址建立连接")
        return configured_connection()

    @app.get("/api/state")
    async def state():
        return {**client.state(), "video": {k: v.status() for k, v in video.streams.items()}}

    @app.get("/api/capabilities")
    async def capabilities():
        return client.capabilities()

    @app.get("/api/diagnostics")
    async def diagnostics():
        return client.diagnostics()

    @app.get("/api/events")
    async def get_events():
        return list(events)

    @app.get("/api/video/stream")
    async def stream(request: Request, camera: Literal["left", "right"] = "left"):
        subscription = video.streams[camera].subscribe()
        queue = await subscription.__aenter__()
        try:
            first = await first_frame(request, queue)
        except BaseException:
            await subscription.__aexit__(None, None, None)
            raise

        async def frames():
            frame = first
            try:
                while True:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                    frame = await next_frame(queue)
            except BridgeError as exc:
                event("video.stream", "error", exc.detail)

        return VideoResponse(frames(), subscription)

    @app.post("/api/snapshots")
    async def capture(body: SnapshotBody):
        async with snapshot_lock:
            started = time.monotonic()
            if body.source == "sdk":
                data, _ = await client.frame(body.camera)
            else:
                data = await video.frame(body.camera)
            result = snapshots.save(data, body.camera, body.source)
            event(
                "camera.capture",
                "success",
                f"{'SDK' if body.source == 'sdk' else 'RTSP'} · {body.camera} · {result['width']} × {result['height']}",
                round((time.monotonic() - started) * 1000),
            )
            return result

    @app.get("/api/snapshots")
    async def list_snapshots():
        return snapshots.list()

    @app.get("/api/snapshots/{identifier}.jpg")
    async def get_snapshot(identifier: str):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise BridgeError(404, "截图不存在")
        path = snapshots.root / (identifier + ".jpg")
        if not path.is_file():
            raise BridgeError(404, "截图不存在")
        return FileResponse(path, media_type="image/jpeg")

    async def recorded(operation, awaitable):
        started = time.monotonic()
        result = await awaitable
        event(operation, result["status"], result.get("detail"), round((time.monotonic() - started) * 1000))
        return result

    @app.post("/api/commands")
    async def command(body: CommandBody):
        return await recorded(body.kind, client.command(body.kind, body.arguments, body.command_id))

    @app.get("/api/commands/{command_id}")
    async def receipt(command_id: str):
        return client.receipt(command_id)

    @app.post("/api/motion")
    async def motion(body: MotionBody):
        return await recorded(body.action, client.move(body.action, body.command_id))

    @app.post("/api/motion/stop")
    async def stop(body: MotionId):
        return await recorded("motion.stop", client.stop(body.command_id))

    @app.post("/api/motion/heartbeat")
    async def heartbeat(body: MotionId):
        return client.heartbeat(body.command_id)

    @app.get("/api/sdk/catalog")
    async def sdk_catalog():
        return client.interfaces.describe()

    @app.post("/api/sdk/call")
    async def sdk_call(body: InterfaceBody):
        return await recorded(
            "sdk." + body.interface, client.call_interface(body.interface, body.payload, body.command_id)
        )

    @app.get("/api/sdk/topics/{name}")
    async def sdk_topic(name: str):
        return await client.topic(name)

    @app.websocket("/api/sdk/topics/{name}/stream")
    async def sdk_topic_stream(websocket: WebSocket, name: str):
        if not same_origin(websocket.headers):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        tasks = []
        try:
            async with client.interfaces.watch(name) as queue:

                async def send_samples():
                    await websocket.send_json({"type": "subscribed", "interface": name})
                    while True:
                        sample = await queue.get()
                        if sample is None:
                            return
                        await websocket.send_json({"type": "sample", "interface": name, **sample})

                async def disconnected():
                    while True:
                        await websocket.receive_text()

                tasks = [asyncio.create_task(send_samples()), asyncio.create_task(disconnected())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except BridgeError as exc:
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "detail": exc.detail})
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await websocket.close()

    @app.post("/api/sdk/publish")
    async def sdk_publish(body: PublishBody):
        return await recorded(
            "sdk." + body.interface,
            client.publish_interface(
                body.interface,
                body.payload,
                command_id=body.command_id,
                rate_hz=body.rate_hz,
                duration_ms=body.duration_ms,
            ),
        )

    @app.get("/api/sdk/stream")
    async def sdk_stream():
        return client.interfaces.stream_status()

    @app.post("/api/sdk/stream/heartbeat")
    async def sdk_stream_heartbeat(body: MotionId):
        return client.stream_heartbeat(body.command_id)

    @app.post("/api/sdk/stream/stop")
    async def sdk_stream_stop(body: MotionId):
        return await recorded("sdk.stream.stop", client.stop_stream(body.command_id))

    @app.post("/api/sdk/audio/play")
    async def sdk_audio_play(
        request: Request, command_id: str, format: Literal["wav", "mp3", "ogg", "flac", "pcm"] = "wav"
    ):
        from t1_lab.sdk.playback import MAX_UPLOAD

        client.require_write()
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_UPLOAD:
                raise BridgeError(413, "音频文件上限 16 MiB")
            data.extend(chunk)
        return await recorded("sdk.audio.play", client.play_audio(bytes(data), format, command_id))

    @app.post("/api/sdk/audio/stop")
    async def sdk_audio_stop(body: MotionId):
        client.require_write()
        return await recorded("sdk.audio.stop", client.audio_player.stop(body.command_id))

    @app.websocket("/api/audio")
    async def audio(websocket: WebSocket):
        if not same_origin(websocket.headers):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        capture_session = None

        async def send_audio():
            nonlocal capture_session
            capture_session = await client.audio.open_continuous({"capture_id": uuid.uuid4().hex})
            await websocket.send_json(
                {"type": "format", "sample_rate": 16000, "channels": 1, "format": "s16le"}
            )
            event("audio.listen", "success", "开始监听机器人麦克风")
            async for chunk in capture_session.chunks():
                await asyncio.wait_for(websocket.send_bytes(chunk), 3)

        async def receive():
            while True:
                message = await asyncio.wait_for(websocket.receive_text(), 10)
                if message != "ping":
                    raise BridgeError(422, "音频连接只接受 ping 心跳")
                if capture_session:
                    capture_session.renew()

        tasks = [asyncio.create_task(send_audio()), asyncio.create_task(receive())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:
            detail = exc.detail if isinstance(exc, BridgeError) else "音频连接中断或超时"
            event("audio.listen", "error", detail)
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "detail": detail})
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if capture_session:
                await capture_session.close()
            with contextlib.suppress(Exception):
                await websocket.close()
            event("audio.listen", "closed", "音频采集已结束")

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    else:

        @app.get("/")
        async def not_built():
            return {
                "detail": "请先执行 npm --prefix web run build，或启动 Vite 开发服务器",
                "api_docs": "/docs",
            }

    return app
