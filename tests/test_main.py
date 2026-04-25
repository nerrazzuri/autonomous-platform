from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def main_module(monkeypatch: pytest.MonkeyPatch):
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_create_uvicorn_config(main_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_config",
        lambda: SimpleNamespace(api=SimpleNamespace(host="127.0.0.1", port=9090)),
    )

    config = main_module.create_uvicorn_config()

    assert config == {
        "app": "apps.logistics.api.rest:app",
        "host": "127.0.0.1",
        "port": 9090,
        "reload": False,
    }


def test_main_importable_without_running_server(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeUvicorn:
        def run(self, **kwargs):
            raise AssertionError("uvicorn.run should not execute during import")

    sys.modules.pop("main", None)
    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn())

    module = importlib.import_module("main")

    assert callable(module.main)
    assert callable(module.startup_system)
    assert callable(module.shutdown_system)
    assert callable(module.create_uvicorn_config)
