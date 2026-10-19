import asyncio
from pathlib import Path

import pytest

from t1_lab._vendor.contracts.catalog import BridgeError
from t1_lab.media import Snapshots, VideoStream, next_frame
from t1_lab.api import first_frame


async def test_disconnect_before_first_video_frame_cancels_pending_wait():
    class Request:
        async def receive(self):
            return {"type": "http.disconnect"}

    with pytest.raises(BridgeError) as error:
        await asyncio.wait_for(first_frame(Request(), asyncio.Queue()), 0.2)
    assert error.value.status == 499


async def test_two_subscribers_share_decoder_and_drop_old_frames(settings, monkeypatch):
    stream = VideoStream(settings, "left")
    started = asyncio.Event()
    stopped = asyncio.Event()
    runs = []

    async def decode():
        runs.append(1)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(stream, "_decode", decode)
    async with stream.subscribe() as first:
        await started.wait()
        async with stream.subscribe() as second:
            stream._broadcast(b"old")
            stream._broadcast(b"new")
            assert await next_frame(first) == await next_frame(second) == b"new"
            assert len(runs) == 1 and len(stream.subscribers) == 2
        assert not stopped.is_set() and len(stream.subscribers) == 1
    assert stopped.is_set() and stream.task is None and not stream.subscribers


async def test_video_error_is_delivered_instead_of_stale_image(settings, monkeypatch):
    stream = VideoStream(settings, "left")

    async def failed_decode():
        stream._broadcast(BridgeError(502, "fixture disconnected"))

    monkeypatch.setattr(stream, "_decode", failed_decode)
    async with stream.subscribe() as queue:
        with pytest.raises(BridgeError, match="fixture disconnected"):
            await next_frame(queue)
    assert stream.latest is None and stream.task is None


def test_snapshot_retention_removes_both_files(tmp_path):
    images = Snapshots(tmp_path, limit=2)
    jpeg = (Path(__file__).parent / "fixtures/frame.jpg").read_bytes()
    first = images.save(jpeg, "left", "sdk")
    images.save(jpeg, "right", "video")
    images.save(jpeg, "left", "video")
    assert len(images.list()) == len(list(images.root.glob("*.jpg"))) == 2
    assert not (images.root / (first["id"] + ".jpg")).exists()
    assert not (images.root / (first["id"] + ".json")).exists()


def test_snapshot_rejects_non_jpeg_and_oversized_dimensions(tmp_path):
    images = Snapshots(tmp_path)
    with pytest.raises(BridgeError):
        images.save(b"not an image", "left", "sdk")
    assert images.list() == []
