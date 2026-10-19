import base64
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from t1_lab._vendor.adapters.audio import PulseAudio
from t1_lab.sdk import Settings, T1Client


class FakeAdapter:
    """In-memory device: never imports rclpy, opens sockets or starts processes."""

    def __init__(self):
        self.audio = PulseAudio({})
        self.start = AsyncMock()
        self.close = AsyncMock()
        self.command = AsyncMock(
            return_value={"status": "accepted", "execution_confirmed": False, "detail": "FAKE receipt"}
        )
        jpeg = (Path(__file__).parent / "fixtures/frame.jpg").read_bytes()
        self.frame = AsyncMock(
            return_value={"image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}
        )

    def state(self):
        return {
            "battery": [
                {
                    "key": "remaining_capacity_percentage",
                    "label": "电量",
                    "value": None,
                    "availability": "unknown",
                    "unit": "%",
                    "source": "test",
                }
            ]
        }

    def evidence(self):
        return {"source_verified": False, "source_count": 0}

    def capabilities(self):
        return []


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=str(tmp_path), read_only=True)


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def sdk(settings, adapter):
    return T1Client(settings, adapter=adapter)
