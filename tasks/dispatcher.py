"""Backward-compatibility alias for apps.logistics.tasks.dispatcher.

This shim exists so legacy imports continue to work.
New code should import from apps.logistics.tasks.dispatcher directly.
"""

from apps.logistics.tasks import dispatcher as _impl
import sys as _sys

_sys.modules[__name__] = _impl
