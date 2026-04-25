from shared.quadruped import sdk_adapter as _impl
import sys as _sys

_sys.modules[__name__] = _impl
