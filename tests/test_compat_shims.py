from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_module(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def _import_old_and_new(old_name: str, new_name: str):
    sys.modules.pop(old_name, None)
    sys.modules.pop("hardware", None)
    old_module = importlib.import_module(old_name)
    new_module = importlib.import_module(new_name)
    return old_module, new_module


def test_hardware_package_compatibility_exports_still_work() -> None:
    hardware_pkg = _reload_module("hardware")

    assert hardware_pkg is importlib.import_module("shared.hardware")
    assert hardware_pkg.GPIORelay is importlib.import_module("shared.hardware.gpio_relay").GPIORelay
    assert hardware_pkg.VideoReader is importlib.import_module("shared.hardware.video_reader").VideoReader
    assert hardware_pkg.QRAnchorReader is importlib.import_module("shared.hardware.qr_anchor").QRAnchorReader
    assert hardware_pkg.MESBridge is importlib.import_module("shared.hardware.mes_bridge").MESBridge


def test_hardware_submodule_identity_matches_shared_modules() -> None:
    old_gpio, new_gpio = _import_old_and_new("hardware.gpio_relay", "shared.hardware.gpio_relay")
    old_video, new_video = _import_old_and_new("hardware.video_reader", "shared.hardware.video_reader")
    old_qr, new_qr = _import_old_and_new("hardware.qr_anchor", "shared.hardware.qr_anchor")
    old_mes, new_mes = _import_old_and_new("hardware.mes_bridge", "shared.hardware.mes_bridge")

    assert old_gpio is new_gpio
    assert old_video is new_video
    assert old_qr is new_qr
    assert old_mes is new_mes


def test_old_hardware_import_paths_still_work() -> None:
    from hardware import GPIORelay, MESBridge, QRAnchorReader, VideoReader
    from hardware.gpio_relay import GPIORelay as SubmoduleGPIORelay
    from hardware.mes_bridge import MESBridge as SubmoduleMESBridge
    from hardware.qr_anchor import QRAnchorReader as SubmoduleQRAnchorReader
    from hardware.video_reader import VideoReader as SubmoduleVideoReader

    assert GPIORelay is SubmoduleGPIORelay
    assert VideoReader is SubmoduleVideoReader
    assert QRAnchorReader is SubmoduleQRAnchorReader
    assert MESBridge is SubmoduleMESBridge
