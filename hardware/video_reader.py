import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("shared.hardware.video_reader")
_sys.modules[__name__] = _impl
