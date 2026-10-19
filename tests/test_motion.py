"""Only in-memory RosAdapter instances are used. No rclpy initialization or real hardware."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from t1_lab._vendor.adapters.audio import PulseAudio
from t1_lab._vendor.adapters.ros.adapter import RosAdapter
from t1_lab._vendor.contracts.catalog import BridgeError
from t1_lab.sdk import T1Client


@pytest.fixture
def fake_motion():
    adapter = RosAdapter.__new__(RosAdapter)
    adapter.audio = PulseAudio({})
    adapter.start, adapter.close = AsyncMock(), AsyncMock()
    adapter.motion_source = "roboweave_" + "a" * 24
    adapter.motion_available = lambda *_: True
    adapter.motion_generation = lambda: 1
    mode, source = [300], ["app_proxy"]
    calls = []
    moved = asyncio.Event()
    adapter.motion_mode = AsyncMock(
        side_effect=lambda: {"mode": mode[0], "status": 100, "switch_ready": True}
    )
    adapter.motion_control = AsyncMock(
        side_effect=lambda: {"name": source[0], "priority": 0, "timeout_ms": 1000}
    )

    async def set_mode(target, *, permit):
        assert permit()
        calls.append(("mode", target))
        mode[0] = {10: 300, 20: 102}.get(target, target)
        return {"status": "accepted"}

    async def acquire(*_, permit):
        assert permit()
        calls.append("acquire")
        source[0] = adapter.motion_source
        return {"status": "accepted"}

    async def velocity(value, *, permit):
        if not permit():
            raise BridgeError(409, "expired test lease")
        calls.append(tuple(value))
        if any(value):
            moved.set()

    async def release(*_, permit):
        assert permit()
        calls.append("release")
        source[0] = "app_proxy"
        return {"status": "accepted"}

    adapter.motion_stop_mode = AsyncMock(side_effect=set_mode)
    adapter.motion_acquire = AsyncMock(side_effect=acquire)
    adapter.motion_velocity = AsyncMock(side_effect=velocity)
    adapter.motion_release = AsyncMock(side_effect=release)
    adapter.motion_wave = AsyncMock(return_value={"status": "accepted", "execution_confirmed": False})
    return adapter, calls, moved, mode, source


@pytest.mark.parametrize(
    "action,axis,sign",
    [
        ("walk_forward_short", 0, 1),
        ("walk_backward_short", 0, -1),
        ("move_left_short", 1, 1),
        ("move_right_short", 1, -1),
        ("turn_left_short", 2, 1),
        ("turn_right_short", 2, -1),
    ],
)
async def test_bounded_motion_stops_and_releases(settings, fake_motion, action, axis, sign):
    adapter, calls, _, _, _ = fake_motion
    settings = replace(
        settings,
        read_only=False,
        motion={"runtime": {"version": 2, "move_duration_ms": 100, "stop_zero_ms": 100}},
    )
    async with T1Client(settings, adapter=adapter) as sdk:
        identifier = sdk.new_command_id()
        assert (await sdk.move(action, identifier))["status"] == "accepted"
        await asyncio.wait_for(sdk.motion_runtime.worker, 3)
        result = sdk.receipt(identifier)
        assert result["status"] == "stopped" and result["output_finished"]
        assert not result["execution_confirmed"]  # No fabricated physical completion.
        vectors = [v for v in calls if isinstance(v, tuple) and len(v) == 3 and any(v)]
        assert vectors and all(v[axis] * sign > 0 for v in vectors)
        assert all(v[i] == 0 for v in vectors for i in range(3) if i != axis)
        assert calls[-1] == "release" and (0, 0, 0) in calls
        count = len(calls)
        assert (await sdk.move(action, identifier))["status"] == "stopped"
        assert len(calls) == count


async def test_stop_and_sdk_shutdown_finish_ongoing_fake_motion(settings, fake_motion):
    adapter, calls, moved, _, _ = fake_motion
    settings = replace(
        settings,
        read_only=False,
        motion={"runtime": {"version": 2, "move_duration_ms": 1000, "stop_zero_ms": 100}},
    )
    async with T1Client(settings, adapter=adapter) as sdk:
        identifier = sdk.new_command_id()
        await sdk.move("walk_forward_short", identifier)
        await asyncio.wait_for(moved.wait(), 2)
        result = await sdk.stop(sdk.new_command_id())
        assert result["status"] == "stop_requested"
        await asyncio.wait_for(sdk.motion_runtime.worker, 2)
        assert sdk.receipt(identifier)["output_finished"]
    assert calls[-1] == "release"


async def test_preset_receipt_does_not_claim_animation_finished(settings, fake_motion):
    adapter, calls, _, _, _ = fake_motion
    async with T1Client(replace(settings, read_only=False), adapter=adapter) as sdk:
        identifier = sdk.new_command_id()
        await sdk.move("wave_right", identifier)
        await asyncio.wait_for(sdk.motion_runtime.worker, 2)
        result = sdk.receipt(identifier)
        assert result["status"] == "accepted" and result["execution_confirmed"] is False
        adapter.motion_wave.assert_awaited_once()
        adapter.motion_velocity.assert_not_awaited()


async def test_active_other_controller_is_not_taken_over(settings, fake_motion):
    adapter, calls, _, _, source = fake_motion
    source[0] = "other-sdk-controller"
    async with T1Client(replace(settings, read_only=False), adapter=adapter) as sdk:
        identifier = sdk.new_command_id()
        await sdk.move("walk_forward_short", identifier)
        await asyncio.wait_for(sdk.motion_runtime.worker, 2)
        assert sdk.receipt(identifier)["status"] == "failed"
        assert not calls
