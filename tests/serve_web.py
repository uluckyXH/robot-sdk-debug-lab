"""Browser fixture server: only the fake adapter, never the native SDK."""

import tempfile

import uvicorn

from conftest import FakeAdapter
from t1_lab.api import create_app
from t1_lab.sdk import Settings, T1Client

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(data_dir=directory, read_only=True)
        uvicorn.run(
            create_app(settings, client=T1Client(settings, adapter=FakeAdapter())),
            host="127.0.0.1",
            port=8091,
        )
