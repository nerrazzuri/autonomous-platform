"""Backward-compatibility alias for apps.logistics.tasks.queue.

This shim exists so legacy imports continue to work.
New code should import from apps.logistics.tasks.queue directly.
"""

from apps.logistics.tasks import queue as _impl
import sys as _sys

_sys.modules[__name__] = _impl
