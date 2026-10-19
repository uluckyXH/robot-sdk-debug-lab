from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from t1_lab._vendor.adapters.ros.adapter import RosAdapter
from t1_lab._vendor.contracts.catalog import BridgeError
from t1_lab.api import create_app
from t1_lab.sdk import Settings, T1Client


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/commands", {"kind": "speak", "arguments": {"text": "must never be spoken"}}),
        ("/api/commands", {"kind": "set_volume", "arguments": {"volume": 25}}),
        ("/api/commands", {"kind": "set_mute", "arguments": {"muted": True}}),
        ("/api/commands", {"kind": "expression", "arguments": {"emotion_id": 10}}),
        ("/api/motion", {"action": "stand_biped"}),
        ("/api/motion/stop", {}),
        ("/api/motion/heartbeat", {}),
    ],
)
def test_every_write_is_blocked_in_readonly_mode(settings, sdk, adapter, path, body):
    with TestClient(create_app(settings, client=sdk)) as http:
        response = http.post(path, json={**body, "command_id": sdk.new_command_id()})
        assert response.status_code == 403
        assert "只读" in response.json()["detail"]
        assert http.get("/api/state").json()["read_only"] is True
    adapter.command.assert_not_awaited()


@pytest.mark.parametrize(
    "kind", ["speak", "set_volume", "set_mute", "expression", "motion_mode", "motion_input", "motion_preset"]
)
async def test_native_guard_runs_before_any_dispatch(settings, kind):
    adapter = RosAdapter.__new__(RosAdapter)
    adapter.config = settings.native()
    with pytest.raises(BridgeError) as exc:
        await adapter._call(kind, {}, "test")
    assert exc.value.status == 403


async def test_readonly_rejects_even_zero_velocity(settings):
    adapter = RosAdapter.__new__(RosAdapter)
    adapter.config = settings.native()
    with pytest.raises(BridgeError) as exc:
        await adapter.motion_velocity((0, 0, 0), permit=lambda: True)
    assert exc.value.status == 403


async def test_direct_sdk_write_guard_and_snapshot(sdk, adapter):
    async with sdk:
        with pytest.raises(BridgeError):
            await sdk.command("speak", {"text": "blocked"}, sdk.new_command_id())
        with pytest.raises(BridgeError):
            await sdk.move("sit_down", sdk.new_command_id())
        with pytest.raises(BridgeError):
            await sdk.stop(sdk.new_command_id())
        image, _ = await sdk.frame("right")
        assert image.startswith(b"\xff\xd8")
        adapter.frame.assert_awaited_with("head_stereo_right")
    adapter.command.assert_not_awaited()


async def test_duplicate_sdk_start_does_not_initialize_another_native_adapter(settings, sdk, adapter):
    async with sdk:
        adapter.start.reset_mock()
        duplicate = T1Client(settings, adapter=adapter)
        with pytest.raises(ValueError, match="已由另一桥接进程使用"):
            await duplicate.start()
        adapter.start.assert_not_awaited()


def test_sdk_capture_persists_jpeg_and_metadata(settings, sdk):
    with TestClient(create_app(settings, client=sdk)) as http:
        result = http.post("/api/snapshots", json={"source": "sdk", "camera": "left"})
        assert result.status_code == 200
        capture = result.json()
        assert capture["width"] == 64 and capture["height"] == 48
        assert http.get(capture["url"]).headers["content-type"] == "image/jpeg"
        assert http.get("/api/snapshots").json()[0]["id"] == capture["id"]
        assert http.get("/api/snapshots/invalid.jpg").status_code == 404
        assert http.post("/api/snapshots", json={"camera": "unknown"}).status_code == 422
        assert http.post("/api/snapshots", json={"source": "sdk", "url": "rtsp://other"}).status_code == 422


def test_command_idempotency_validation_and_cross_origin(settings, adapter):
    settings = replace(settings, read_only=False)
    sdk = T1Client(settings, adapter=adapter)
    with TestClient(create_app(settings, client=sdk)) as http:
        body = {"command_id": sdk.new_command_id(), "kind": "set_volume", "arguments": {"volume": 20}}
        response = http.post("/api/commands", json=body)
        assert response.status_code == 200 and response.json()["status"] == "accepted"
        assert not response.json()["execution_confirmed"]
        assert http.post("/api/commands", json=body).json() == response.json()
        adapter.command.assert_awaited_once()
        assert http.post("/api/commands", json={**body, "arguments": {"volume": 21}}).status_code == 409
        assert (
            http.post(
                "/api/commands",
                json={**body, "command_id": sdk.new_command_id(), "arguments": {"volume": True}},
            ).status_code
            == 422
        )
        assert (
            http.post("/api/commands", json=body, headers={"Origin": "https://untrusted.example"}).status_code
            == 403
        )
        adapter.command.assert_awaited_once()


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_readonly_setting_requires_explicit_boolean(value):
    with pytest.raises(ValueError):
        Settings(read_only=value)


def test_audio_origin_and_unconfigured_error(settings, sdk):
    with TestClient(create_app(settings, client=sdk)) as http:
        with http.websocket_connect("/api/audio") as websocket:
            message = websocket.receive_json()
            assert message["type"] == "error" and "未配置" in message["detail"]
        with pytest.raises(Exception):
            with http.websocket_connect("/api/audio", headers={"Origin": "https://untrusted.example"}):
                pass
