from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_importing_main_does_not_launch_app() -> None:
    module = importlib.import_module("main")

    assert callable(module.main)


def test_main_keeps_logistics_startup_compatibility_exports() -> None:
    module = importlib.import_module("main")

    assert callable(module.startup_system)
    assert callable(module.shutdown_system)
    assert module.base_startup is not None


def test_app_selector_defaults_to_logistics() -> None:
    module = importlib.import_module("main")
    parser = module._build_parser()

    args = parser.parse_args([])

    assert args.app == "logistics"


def test_main_launches_selected_app(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("main")
    calls: list[str] = []

    def load_app_main(app_name: str):
        def app_main() -> None:
            calls.append(app_name)

        return app_main

    monkeypatch.setattr(module, "_load_app_main", load_app_main)

    module.main(["--app", "patrol"])

    assert calls == ["patrol"]


def test_main_default_launches_logistics(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("main")
    calls: list[str] = []

    def load_app_main(app_name: str):
        def app_main() -> None:
            calls.append(app_name)

        return app_main

    monkeypatch.setattr(module, "_load_app_main", load_app_main)

    module.main([])

    assert calls == ["logistics"]
