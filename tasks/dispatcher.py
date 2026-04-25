from apps.logistics.tasks import dispatcher as _impl
import sys as _sys

_sys.modules[__name__] = _impl
