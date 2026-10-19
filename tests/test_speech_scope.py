"""Speech-only mode must never initialize or dispatch motion controls."""

from dataclasses import replace

import pytest

from t1_lab._vendor.adapters.ros.adapter import RosAdapter, enabled_services, motion_enabled
from t1_lab._vendor.contracts.catalog import BridgeError, READ_SERVICES
from t1_lab.sdk import T1Client


@pytest.fixture
def speech_settings(settings):
    return replace(settings, read_only=False, enabled_commands=("speak",), motion={"enabled": False})


def test_speech_scope_creates_only_read_and_tts_clients(speech_settings):
    config = speech_settings.native()
    assert set(enabled_services(config)) == {*READ_SERVICES, "frame", "speak"}
    assert not motion_enabled(config)
    assert set(enabled_services(replace(config, read_only=True))) == {*READ_SERVICES, "frame"}


@pytest.mark.parametrize(
    "kind", ["motion_mode", "motion_preset", "motion_input", "expression", "set_volume", "set_mute"]
)
async def test_native_speech_scope_rejects_other_writes_before_dispatch(speech_settings, kind):
    adapter = RosAdapter.__new__(RosAdapter)
    adapter.config = speech_settings.native()
    with pytest.raises(BridgeError) as exc:
        await adapter._call(kind, {}, "unused")
    assert exc.value.status == 403


async def test_speech_scope_forbids_even_zero_velocity(speech_settings):
    adapter = RosAdapter.__new__(RosAdapter)
    adapter.config = speech_settings.native()
    with pytest.raises(BridgeError) as exc:
        await adapter.motion_velocity((0, 0, 0), permit=lambda: True)
    assert exc.value.status == 403


async def test_public_sdk_speech_scope(speech_settings, adapter):
    async with T1Client(speech_settings, adapter=adapter) as sdk:
        result = await sdk.command("speak", {"text": "offline test"}, sdk.new_command_id())
        assert result["status"] == "accepted"
        with pytest.raises(BridgeError):
            await sdk.command("set_volume", {"volume": 20}, sdk.new_command_id())
        with pytest.raises(BridgeError):
            await sdk.move("sit_down", sdk.new_command_id())
        with pytest.raises(BridgeError):
            await sdk.stop(sdk.new_command_id())
        with pytest.raises(BridgeError):
            sdk.heartbeat(sdk.new_command_id())
        assert sdk.motion_runtime.active is None
    adapter.command.assert_awaited_once()
