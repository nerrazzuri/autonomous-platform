from shared.api import ws_broker as _impl
import sys as _sys

_sys.modules[__name__] = _impl
