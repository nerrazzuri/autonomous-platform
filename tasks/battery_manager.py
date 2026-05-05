"""Backward-compatibility alias for apps.logistics.tasks.battery_manager.

This shim exists so legacy imports continue to work.
New code should import from apps.logistics.tasks.battery_manager directly.
"""

from apps.logistics.tasks import battery_manager as _impl
import sys as _sys

_sys.modules[__name__] = _impl
