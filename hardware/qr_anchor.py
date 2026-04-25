import importlib as _importlib
import sys as _sys

_impl = _importlib.import_module("shared.hardware.qr_anchor")
_sys.modules[__name__] = _impl
