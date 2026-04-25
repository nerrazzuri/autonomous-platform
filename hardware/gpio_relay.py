import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("shared.hardware.gpio_relay")
_sys.modules[__name__] = _impl
