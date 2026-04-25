import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("shared.hardware")

for _old_name, _new_name in {
    "gpio_relay": "shared.hardware.gpio_relay",
    "video_reader": "shared.hardware.video_reader",
    "qr_anchor": "shared.hardware.qr_anchor",
    "mes_bridge": "shared.hardware.mes_bridge",
}.items():
    _sys.modules[f"{__name__}.{_old_name}"] = _importlib.import_module(_new_name)

_sys.modules[__name__] = _impl
