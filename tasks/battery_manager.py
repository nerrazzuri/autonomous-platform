from apps.logistics.tasks import battery_manager as _impl
import sys as _sys

_sys.modules[__name__] = _impl
