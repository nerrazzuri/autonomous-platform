from apps.logistics.tasks import watchdog as _impl
import sys as _sys

_sys.modules[__name__] = _impl
